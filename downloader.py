"""
downloader.py —— 视频音频下载模块（多平台：YouTube / 哔哩哔哩 / 抖音 / ...）
使用 yt-dlp CLI 下载视频的音频轨道。
采用 CLI 方式（而非 Python API）以确保 --js-runtimes / --remote-components 等
新参数的兼容性。

多平台说明：
    - 通过 providers.BaseProvider 注入平台差异（cookies 文件名、player_client、deno、
      额外 yt-dlp 参数等）
    - 未传 provider 时默认走 YouTube，保持与老调用方向后兼容
    - **抖音专属增强通路**：当检测到 provider 是 douyin 且按需依赖 f2 已安装时，
      优先走 providers.douyin_f2（能绕过抖音风控）。失败/未装时透明回退 yt-dlp。
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from cookies_utils import ensure_netscape
from providers import BaseProvider, ALL_PROVIDERS, resolve_provider
from providers.youtube import YouTubeProvider


# 优先级：函数参数 > 环境变量 YTDLP_BROWSER > 默认 edge（Windows 常见）
DEFAULT_BROWSER = os.environ.get("YTDLP_BROWSER", "edge")

# 通用兜底 cookies 候选（当 provider 没给专属 cookies 或文件不存在时的回退）
GENERIC_COOKIES_CANDIDATES = [
    "cookies.txt",
    "cookies.json",
]


def _cookies_candidates_for(provider: Optional[BaseProvider]) -> list[str]:
    """按 provider 组装本地 cookies 文件候选列表（按优先级）。"""
    names: list[str] = []
    if provider and provider.cookies_filename:
        # 主文件名 + 把 .json 换成 .txt 的同名兜底
        main = provider.cookies_filename
        names.append(main)
        if main.endswith(".json"):
            names.append(main[:-5] + ".txt")
        elif main.endswith(".txt"):
            names.append(main[:-4] + ".json")
    names.extend(GENERIC_COOKIES_CANDIDATES)
    # 去重但保留顺序
    seen = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out


def _cookie_file_has_content(path: Path) -> bool:
    """判断 cookies 文件是否有实际内容（避免空占位的 [] / {} 被误当可用源）。"""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return False
        # 常见空占位：[]、{}、{"cookies": []}
        if text in ("[]", "{}"):
            return False
        if text.startswith("{") or text.startswith("["):
            try:
                import json as _json
                data = _json.loads(text)
                if isinstance(data, list):
                    return len(data) > 0
                if isinstance(data, dict):
                    if "cookies" in data:
                        return bool(data["cookies"])
                    # 其他结构：只要有任何 list 值非空就算有内容
                    for v in data.values():
                        if isinstance(v, list) and v:
                            return True
                    return False
            except Exception:
                # 解析失败但有内容也先当有效，让下游报更准的错
                return True
        # Netscape 纯文本：至少有一行非注释内容
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return True
        return False
    except OSError:
        return False


def _cookies_failure_hint(provider: Optional[BaseProvider]) -> str:
    p = provider or YouTubeProvider()
    main_name = p.cookies_filename or "cookies.json"
    txt_name = main_name[:-5] + ".txt" if main_name.endswith(".json") else main_name

    lines = [
        f"\n所有 cookies 来源尝试后仍失败（平台：{p.display_name}）。请选择任一方式解决：",
        f"  1) 在项目根目录放置 cookies 文件（推荐）：",
        f"     • JSON 格式（推荐）：{main_name}（J2TEAM Cookies 扩展导出，脚本会自动转换）",
        f"     • Netscape 格式   ：{txt_name}（Get cookies.txt LOCALLY 扩展）",
    ]
    if p.name == "youtube":
        lines += [
            "  2) 或安装 Firefox 并登录 YouTube，再用 --browser firefox 运行",
            "  3) 如需 Edge/Chrome 自动读取：装插件 yt-dlp-ChromeCookieUnlock + 关闭浏览器",
            "     （Chromium 127+ 的 App-Bound Encryption 会阻止 DPAPI 解密）",
        ]
    else:
        lines += [
            f"  2) 或确保浏览器已登录 {p.display_name} 后用 --browser firefox 运行",
        ]
    return "\n".join(lines)


def _has_chrome_cookie_unlock() -> bool:
    """
    检测是否装了 yt-dlp-ChromeCookieUnlock 插件。
    装了之后可以绕过 Chromium 127+ 的 App-Bound Encryption，
    直接用 `--cookies-from-browser edge` 自动解密，无需手动导出 cookies。
    """
    try:
        import yt_dlp_plugins.postprocessor as _pp  # noqa: F401
        import pkgutil
        for m in pkgutil.iter_modules(_pp.__path__):
            if "chrome_cookie_unlock" in m.name.lower():
                return True
    except Exception:
        pass
    return False


def _is_auth_error(err_text: str) -> bool:
    """判断是不是 YouTube 反爬/登录错误。"""
    if not err_text:
        return False
    low = err_text.lower()
    markers = [
        "sign in to confirm",
        "sign in to prove",
        "confirm you're not a bot",
        "not a bot",
        "http error 403",
        "http error 429",
        "login required",
        "unable to extract",
        "fresh cookies",   # 抖音 extractor：Fresh cookies ... are needed
    ]
    return any(m in low for m in markers)


def _is_browser_decrypt_error(err_text: str) -> bool:
    """判断是不是浏览器 cookies 解密失败（App-Bound Encryption）。"""
    if not err_text:
        return False
    low = err_text.lower()
    markers = [
        "failed to decrypt with dpapi",
        "could not decrypt",
        "app-bound",
        "could not copy",
        "unable to read",
    ]
    return any(m in low for m in markers)



def _safe_filename(name: str) -> str:
    """清理文件名中的非法字符。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip()[:120] or "video"


def _find_deno() -> Optional[str]:
    """查找 Deno 可执行文件路径：优先本地 bin/deno.exe，其次系统 PATH。"""
    script_dir = Path(__file__).parent
    for c in [script_dir / "bin" / "deno.exe", script_dir / "bin" / "deno"]:
        if c.exists():
            return str(c)
    return shutil.which("deno")


def _ensure_deno_on_path() -> Optional[str]:
    """如果本地有 Deno，加到 PATH 最前面；返回 deno 路径或 None。"""
    deno_path = _find_deno()
    if not deno_path:
        return None
    bin_dir = str(Path(deno_path).parent)
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + current
    return deno_path


def _build_base_args(
    verbose: bool,
    browser: Optional[str],
    cookies_file: Optional[str],
    provider: Optional[BaseProvider] = None,
) -> list[str]:
    """构建传给 yt-dlp CLI 的通用参数。

    provider 参数用于控制平台相关的行为：
      - YouTube：启用 player_client 回退列表、deno JS runtime
      - 其他平台：跳过 YouTube 专属参数
    未传 provider 时为兼容老调用，默认按 YouTube 处理。
    """
    if provider is None:
        provider = YouTubeProvider()

    args: list[str] = ["--no-playlist", "--retries", "5", "--socket-timeout", "30"]

    if not verbose:
        args += ["--quiet", "--no-warnings"]

    # YouTube 专属：多 player_client 回退
    if provider.name == "youtube":
        if cookies_file or browser:
            clients = "web,web_safari,mweb"
        else:
            clients = "android,ios,web_safari,web"
        args += ["--extractor-args", f"youtube:player_client={clients}"]

    # JS 运行时（Deno）—— 只有需要 n-sig 解密的平台才加
    if provider.needs_deno:
        deno_path = _ensure_deno_on_path()
        if deno_path:
            args += ["--js-runtimes", "deno"]
            args += ["--remote-components", "ejs:github"]

    # 平台自定义 flag（如抖音 Referer）
    if provider.extra_ytdlp_args:
        args += list(provider.extra_ytdlp_args)

    # Cookies
    if cookies_file:
        args += ["--cookies", str(cookies_file)]
    elif browser:
        args += ["--cookies-from-browser", browser]

    return args


def _run_ytdlp(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """调用 `python -m yt_dlp ...`，保证用的是当前环境的 yt-dlp。"""
    cmd = [sys.executable, "-m", "yt_dlp", *args]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _resolve_cookies_chain(
    browser: Optional[str],
    cookies_file: Optional[str],
    provider: Optional[BaseProvider] = None,
    script_dir: Optional[Path] = None,
) -> list[tuple[Optional[str], Optional[str], str]]:
    """
    根据 provider + 用户传入的 browser / cookies_file 组装 cookies 获取优先级链。

    返回一个列表，每个元素是 `(browser, cookies_file, label)`，
    按顺序尝试，前一个失败就换下一个。

    决策规则：
    1. 用户显式传了 cookies_file → 只用 cookies_file，不降级到浏览器（尊重用户意图）
    2. 用户显式传了 browser="" / "none" → 完全禁用 cookies
    3. 其他情况（含默认）：
       a) 若是 YouTube 且装了 ChromeCookieUnlock 插件 → 先试浏览器
       b) 按 provider.cookies_filename 查项目根下的 cookies 文件（主候选）
       c) 通用兜底（cookies.txt / cookies.json）
       d) 至少保证链表非空，方便错误信息
    """
    script_dir = script_dir or Path(__file__).parent
    if provider is None:
        provider = YouTubeProvider()

    # Case 1: 用户给了 cookies 文件 → 只用它
    if cookies_file:
        return [(None, str(cookies_file), f"cookies 文件 {cookies_file}")]

    # Case 2: 显式禁用
    if browser == "":
        return [(None, None, "无 cookies（已禁用）")]

    # Case 3: 智能链
    chain: list[tuple[Optional[str], Optional[str], str]] = []

    eff_browser = browser or DEFAULT_BROWSER

    # 3a) 浏览器自动读取：目前只有 YouTube 通路有现成的 ChromeCookieUnlock 插件支持；
    #     Firefox 不受 App-Bound 影响，对所有平台都可用
    chromium_family = {"edge", "chrome", "brave", "chromium", "vivaldi", "opera"}
    if eff_browser == "firefox":
        chain.append((eff_browser, None, f"浏览器 {eff_browser} cookies"))
    elif eff_browser in chromium_family and provider.name == "youtube":
        if _has_chrome_cookie_unlock():
            chain.append((eff_browser, None, f"浏览器 {eff_browser} cookies (ChromeCookieUnlock 解锁)"))

    # 3b) 按 provider 的 cookies 候选找第一个存在的
    for candidate in _cookies_candidates_for(provider):
        p = script_dir / candidate
        if p.exists() and _cookie_file_has_content(p):
            chain.append((None, str(p), f"cookies 文件 {candidate}"))
            break  # 找到一个就够

    if not chain:
        chain.append((None, None, "无可用 cookies 源"))

    return chain


def _probe_metadata(
    url: str,
    base_args: list[str],
) -> dict:
    """用 --dump-json 获取视频元数据（不下载）。"""
    result = _run_ytdlp([*base_args, "--dump-json", "--skip-download", url], capture=True)
    if result.returncode != 0 or not result.stdout.strip():
        err = result.stderr or result.stdout
        raise RuntimeError(f"获取视频元数据失败：\n{err[:1000]}")
    # yt-dlp 每个视频输出一行 JSON
    first_line = result.stdout.strip().splitlines()[0]
    return json.loads(first_line)


def probe_metadata_only(
    url: str,
    verbose: bool = True,
    browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    provider: Optional[BaseProvider] = None,
) -> dict:
    """
    只抓视频元数据（不下载音频），用于先决定是否走字幕方案。
    返回 dict: title/video_id/duration/uploader/url/description/
              subtitles/automatic_captions/safe_title

    智能降级：按 _resolve_cookies_chain 的顺序依次尝试，
    遇到认证错误 / 浏览器解密错误自动切换下一个来源，
    最终成功时会把实际采用的来源写回 `_browser` / `_cookies_file`。
    """
    if provider is None:
        provider = resolve_provider(url)

    # ── 抖音专属：优先走 f2 增强通路（按需依赖） ─────────────────────────────────
    if provider.name == "douyin":
        try:
            from providers.douyin_f2 import (
                is_available as _f2_ok,
                f2_probe_metadata,
                F2NotAvailableError,
                F2CookiesRequiredError,
            )
            if _f2_ok():
                # 找一个可用的抖音 cookies 文件（复用已有 cookies 链的解析逻辑）
                _cookies_for_f2 = cookies_file
                if not _cookies_for_f2:
                    script_dir = Path(__file__).parent
                    for candidate in _cookies_candidates_for(provider):
                        p = script_dir / candidate
                        if p.exists() and _cookie_file_has_content(p):
                            _cookies_for_f2 = str(p)
                            break
                if _cookies_for_f2:
                    if verbose:
                        print(f"    [f2] 尝试用 f2 增强通路抓取抖音元数据...")
                    try:
                        meta = f2_probe_metadata(url, cookies_file=_cookies_for_f2, verbose=verbose)
                        if verbose:
                            print(f"    [f2] ✓ 元数据抓取成功：{meta.get('title')!r} by {meta.get('uploader')!r}")
                        return meta
                    except F2CookiesRequiredError as e:
                        if verbose:
                            print(f"    [f2] ⚠️  {e}，回退 yt-dlp 通路")
                    except F2NotAvailableError as e:
                        if verbose:
                            print(f"    [f2] ⚠️  {e}，回退 yt-dlp 通路")
                    except Exception as e:
                        if verbose:
                            print(f"    [f2] ⚠️  f2 抓取失败：{e}\n           回退 yt-dlp 通路")
        except ImportError:
            # 连 douyin_f2 模块都 import 不到（理论上不该发生）
            pass

    chain = _resolve_cookies_chain(browser, cookies_file, provider=provider)

    last_err: Optional[Exception] = None
    used_browser: Optional[str] = None
    used_cookies: Optional[str] = None

    info = None
    for idx, (b, cf, label) in enumerate(chain):
        # cookies 文件格式校验（JSON → Netscape）
        if cf:
            try:
                cf = str(ensure_netscape(cf))
            except Exception as e:
                last_err = RuntimeError(f"cookies 文件格式转换失败: {e}")
                if verbose:
                    print(f"    [yt-dlp] ⚠️  跳过「{label}」：{e}")
                continue

        if verbose:
            print(f"    [yt-dlp] 尝试 cookies 来源：{label}" + (f"（降级第 {idx} 次）" if idx > 0 else ""))

        base_args = _build_base_args(verbose, b, cf, provider=provider)
        try:
            info = _probe_metadata(url, base_args)
            used_browser, used_cookies = b, cf
            break
        except RuntimeError as e:
            last_err = e
            msg = str(e)
            if _is_browser_decrypt_error(msg) and b:
                if verbose:
                    print(f"    [yt-dlp] ⚠️  浏览器 cookies 解密失败，切换下一个来源...")
                continue
            if _is_auth_error(msg) and idx < len(chain) - 1:
                if verbose:
                    print(f"    [yt-dlp] ⚠️  认证失败，切换下一个来源...")
                continue
            # 非 cookies 相关错误直接抛
            raise

    if info is None:
        raise RuntimeError(f"{last_err}{_cookies_failure_hint(provider)}") from last_err

    title = info.get("title", "video")
    video_id = info.get("id", "unknown")
    return {
        "title": title,
        "video_id": video_id,
        "safe_title": _safe_filename(title),
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", ""),
        "url": url,
        "description": (info.get("description") or "")[:500],
        "subtitles": info.get("subtitles") or {},
        "automatic_captions": info.get("automatic_captions") or {},
        # 保留 cookies 源（已转换后的路径）供后续使用，保证同一视频多步骤用同一组 cookies
        "_cookies_file": used_cookies,
        "_browser": used_browser,
        "_provider": provider.name,
    }


def download_audio(
    url: str,
    out_dir: Path,
    verbose: bool = True,
    browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    provider: Optional[BaseProvider] = None,
) -> dict:
    """
    下载视频的音频（通过 yt-dlp CLI，支持多平台）。

    参数：
        url:          视频链接（YouTube / B 站 / 抖音 等）
        out_dir:      音频输出目录
        verbose:      是否输出详细日志
        browser:      从哪个浏览器读取 cookies (chrome/firefox/edge/...).
                      None 时使用 DEFAULT_BROWSER。传空字符串 "" 表示禁用。
        cookies_file: 使用 netscape/JSON 格式的 cookies 文件（优先级最高）
        provider:     开发者可以显式传入平台实例，None 时根据 url 自动识别。

    返回 dict：
        {
            "audio_path", "title", "duration",
            "uploader", "url", "video_id", "description",
        }

    智能降级：按 _resolve_cookies_chain 的顺序依次尝试，
    遇到认证错误 / 浏览器解密错误自动切换下一个来源。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if provider is None:
        provider = resolve_provider(url)

    # ── 抖音专属：优先走 f2 增强通路（按需依赖） ─────────────────────────────────
    if provider.name == "douyin":
        try:
            from providers.douyin_f2 import (
                is_available as _f2_ok,
                f2_probe_metadata,
                f2_download_video,
                F2NotAvailableError,
                F2CookiesRequiredError,
            )
            if _f2_ok():
                _cookies_for_f2 = cookies_file
                if not _cookies_for_f2:
                    script_dir = Path(__file__).parent
                    for candidate in _cookies_candidates_for(provider):
                        p = script_dir / candidate
                        if p.exists() and _cookie_file_has_content(p):
                            _cookies_for_f2 = str(p)
                            break
                if _cookies_for_f2:
                    try:
                        if verbose:
                            print(f"    [f2] 尝试用 f2 增强通路下载抖音视频...")
                        meta = f2_probe_metadata(url, cookies_file=_cookies_for_f2, verbose=verbose)
                        return f2_download_video(
                            url, meta, out_dir,
                            cookies_file=_cookies_for_f2,
                            verbose=verbose,
                        )
                    except (F2NotAvailableError, F2CookiesRequiredError) as e:
                        if verbose:
                            print(f"    [f2] ⚠️  {e}，回退 yt-dlp 通路")
                    except Exception as e:
                        if verbose:
                            print(f"    [f2] ⚠️  f2 下载失败：{e}\n           回退 yt-dlp 通路")
        except ImportError:
            pass

    chain = _resolve_cookies_chain(browser, cookies_file, provider=provider)

    last_err: Optional[Exception] = None
    info = None
    used_b: Optional[str] = None
    used_cf: Optional[str] = None

    # ── 1) 获取元数据（带降级重试）─────────────────────────────────────────
    for idx, (b, cf, label) in enumerate(chain):
        if cf:
            try:
                cf = str(ensure_netscape(cf))
            except Exception as e:
                last_err = RuntimeError(f"cookies 文件格式转换失败: {e}")
                if verbose:
                    print(f"    [yt-dlp] ⚠️  跳过「{label}」：{e}")
                continue

        if verbose:
            print(f"    [yt-dlp] 正在抓取视频元数据...（cookies 来源：{label}" + (f"，降级第 {idx} 次" if idx > 0 else "") + "）")

        base_args = _build_base_args(verbose, b, cf, provider=provider)
        try:
            info = _probe_metadata(url, base_args)
            used_b, used_cf = b, cf
            break
        except RuntimeError as e:
            last_err = e
            msg = str(e)
            if _is_browser_decrypt_error(msg) and b:
                if verbose:
                    print(f"    [yt-dlp] ⚠️  浏览器 cookies 解密失败，切换下一个来源...")
                continue
            if _is_auth_error(msg) and idx < len(chain) - 1:
                if verbose:
                    print(f"    [yt-dlp] ⚠️  认证失败，切换下一个来源...")
                continue
            raise

    if info is None:
        raise RuntimeError(f"{last_err}{_cookies_failure_hint(provider)}") from last_err

    title = info.get("title", "video")
    video_id = info.get("id", "unknown")
    safe_title = _safe_filename(title)
    out_template = str(out_dir / f"{safe_title}_{video_id}.%(ext)s")

    # ── 2) 真正下载（用已确认可用的 cookies 来源）──────────────────────────────────
    if verbose:
        print(f"    [yt-dlp] 开始下载音频 [{title}]...")
    base_args = _build_base_args(verbose, used_b, used_cf, provider=provider)
    dl_args = [
        *base_args,
        "-f", "bestaudio/best",
        "-o", out_template,
        url,
    ]
    # 流式打印下载进度（不 capture）
    result = _run_ytdlp(dl_args, capture=False)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败，退出码 {result.returncode}")

    # ── 3) 找到下载的文件 ────────────────────────────────────────────────────
    candidates = list(out_dir.glob(f"{safe_title}_{video_id}.*"))
    if not candidates:
        raise FileNotFoundError(f"下载成功但找不到音频文件在 {out_dir}")
    # 排除任何非音频文件（避免 .description / .part 之类）
    audio_candidates = [
        c for c in candidates
        if c.suffix.lower() in {".m4a", ".webm", ".mp3", ".opus", ".aac", ".wav", ".ogg"}
    ]
    if audio_candidates:
        audio_path = max(audio_candidates, key=lambda p: p.stat().st_mtime)
    else:
        audio_path = max(candidates, key=lambda p: p.stat().st_mtime)

    return {
        "audio_path": str(audio_path),
        "title": title,
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", ""),
        "url": url,
        "video_id": video_id,
        "description": (info.get("description") or "")[:500],
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python downloader.py <YouTube URL> [--cookies <file>] [--browser <name>]")
        sys.exit(1)
    url = sys.argv[1]
    cookies = None
    browser = None
    for i, a in enumerate(sys.argv):
        if a == "--cookies" and i + 1 < len(sys.argv):
            cookies = sys.argv[i + 1]
        if a == "--browser" and i + 1 < len(sys.argv):
            browser = sys.argv[i + 1]
    meta = download_audio(url, Path("./downloads"), cookies_file=cookies, browser=browser)
    print("下载完成：", meta)
