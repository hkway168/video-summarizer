"""
media_downloader.py —— 纯下载器（只下载，不转写）

与 main.py 的关系：
    main.py            = 下载/字幕 + 转写 + 生成 transcript.md（一键流程）
    media_downloader.py = 只把视频/音频文件拉到本地，完全不碰 Whisper
    transcribe_file.py  = 只对本地已有文件做转写

复用 downloader.py 里已经打磨好的 cookies 降级链、provider 注入、抖音 f2 增强通路，
因此支持的平台与登录能力和主流程完全一致。

用法：
    python media_downloader.py <URL>                          # 下载最佳画质视频
    python media_downloader.py <URL> --quality 1080           # 限制最高 1080p
    python media_downloader.py <URL> --kind audio             # 只要音频
    python media_downloader.py <URL> --kind audio --audio-format mp3
    python media_downloader.py <URL> --subs                   # 同时下载字幕
    python media_downloader.py --file urls.txt -o D:/videos   # 批量
    python media_downloader.py <URL> --json                   # 结构化输出（供 GUI 解析）
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Optional

# 强制 UTF-8，避免 Windows GBK 控制台下 emoji 报错
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from downloader import (  # noqa: E402 - 复用已验证的下载基建
    _build_base_args,
    _cookie_file_has_content,
    _cookies_candidates_for,
    _cookies_failure_hint,
    _is_auth_error,
    _is_browser_decrypt_error,
    _probe_metadata,
    _resolve_cookies_chain,
    _run_ytdlp,
    _safe_filename,
)
from providers import BaseProvider, PROVIDERS_BY_NAME, resolve_provider  # noqa: E402

# ── 常量 ────────────────────────────────────────────────────────────────
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".flv", ".mov", ".avi", ".ts", ".m4v"}
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".webm", ".aac", ".wav", ".ogg", ".flac"}
SUB_EXTS = {".srt", ".vtt", ".ass", ".lrc"}
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

QUALITY_CHOICES = ["best", "2160", "1440", "1080", "720", "480", "360", "worst"]
CONTAINER_CHOICES = ["mp4", "mkv", "keep"]
AUDIO_FORMAT_CHOICES = ["keep", "mp3", "m4a", "wav", "flac"]

DEFAULT_SUB_LANGS = "zh-Hans,zh-Hant,zh,en"


class LoginRequiredError(RuntimeError):
    """缺少有效登录 cookies（与 main.py 的同名异常语义一致）。"""

    def __init__(self, platform: str, reason: str, original_error: str = ""):
        self.platform = platform
        self.reason = reason
        self.original_error = original_error
        super().__init__(f"[{platform}] 需要登录：{reason}")


_LOGIN_HINT_PATTERNS: tuple[str, ...] = (
    "http error 403", "http error 412", "login required", "需要登录", "需登录",
    "请登录", "please login", "sign in", "cookies are no longer valid",
    "account_logout", "invalid cookies", "fresh cookies", "-352", "-101",
    "verify_captcha",
)


def _looks_like_login_required(err_msg: str) -> bool:
    if not err_msg:
        return False
    low = err_msg.lower()
    return any(p in low for p in _LOGIN_HINT_PATTERNS)


def _fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}小时{m}分{s}秒"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── format 表达式 ───────────────────────────────────────────────────────
def build_format_selector(kind: str, quality: str, container: str) -> str:
    """把「画质 + 容器」翻译成 yt-dlp 的 -f 表达式。"""
    if kind == "audio":
        return "bestaudio/best"

    if quality == "worst":
        return "worstvideo*+worstaudio/worst"

    if quality == "best":
        height_v = ""
        height_b = ""
    else:
        height_v = f"[height<={quality}]"
        height_b = f"[height<={quality}]"

    # mp4 容器优先挑 avc1+m4a，兼容性最好（Windows 播放器/剪辑软件都认）
    if container == "mp4":
        return (
            f"bv*[ext=mp4]{height_v}+ba[ext=m4a]/"
            f"bv*{height_v}+ba/"
            f"b[ext=mp4]{height_b}/"
            f"b{height_b}/b"
        )
    return f"bv*{height_v}+ba/b{height_b}/b"


def _classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in SUB_EXTS:
        return "subtitle"
    if ext in THUMB_EXTS:
        return "thumbnail"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def locate_cookies_file(
    provider: Optional[BaseProvider],
    cookies_file: Optional[str] = None,
) -> Optional[str]:
    """定位可用的 cookies 文件：显式参数优先，否则按 provider 候选名在项目根找。

    只返回"确实有内容"的文件（空占位的 [] / {} 会被 _cookie_file_has_content 挡掉）。
    """
    if cookies_file and _cookie_file_has_content(Path(cookies_file)):
        return cookies_file
    if cookies_file:
        return cookies_file          # 用户显式指定，即使看起来是空的也尊重
    script_dir = Path(__file__).parent
    for candidate in _cookies_candidates_for(provider):
        p = script_dir / candidate
        if p.exists() and _cookie_file_has_content(p):
            return str(p)
    return None


def _pick_main_file(files: list[Path], kind: str) -> Optional[Path]:
    """从下载产物里挑出主文件（视频或音频）。"""
    want = "audio" if kind == "audio" else "video"
    primary = [f for f in files if _classify(f) == want]
    if not primary and want == "video":
        # 有些平台只有音频轨，或合流失败后只剩音频
        primary = [f for f in files if _classify(f) == "audio"]
    if not primary:
        primary = [f for f in files if _classify(f) not in ("subtitle", "thumbnail")]
    if not primary:
        return None
    return max(primary, key=lambda p: p.stat().st_size)


# ══════════════════════════════════════════════════════════════════════
# 核心：下载一个 URL
# ══════════════════════════════════════════════════════════════════════
def download_media(
    url: str,
    out_dir: Path,
    kind: str = "video",              # video | audio
    quality: str = "best",
    container: str = "mp4",
    audio_format: str = "keep",
    write_subs: bool = False,
    sub_langs: str = DEFAULT_SUB_LANGS,
    write_thumbnail: bool = False,
    embed_metadata: bool = False,
    browser: Optional[str] = None,
    cookies_file: Optional[str] = None,
    provider: Optional[BaseProvider] = None,
    platform: Optional[str] = None,
    verbose: bool = True,
    skip_existing: bool = True,
) -> dict:
    """把一个 URL 的视频/音频下载到 out_dir，不做任何转写。

    Args:
        skip_existing: 本地已有同名产物时直接复用（批量下载时可增量补全）。
                       yt-dlp 通路本来就会跳过重复，这里主要作用于抖音 f2 通路。

    返回 dict：
        file_path / file_size / kind / title / uploader / duration /
        video_id / url / platform / extra_files
    """
    if provider is None:
        provider = resolve_provider(url, platform=platform)

    normalized = provider.normalize_url(url)
    if normalized != url:
        print(f"🔀 URL 已归一化: {url}\n              → {normalized}")
        url = normalized

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"⬇️  开始下载: {url}")
    print(f"🌐 平台: {provider.display_name} ({provider.name})")
    print(f"🎯 目标: {'音频' if kind == 'audio' else '视频'}"
          f" | 画质: {quality} | 容器: {container}")
    print(f"📁 输出: {out_dir}")
    print(f"{'='*60}")

    # ── 抖音专属：f2 增强通路（yt-dlp 常被风控挡住）──────────────────────
    if provider.name == "douyin" and kind == "video":
        f2_result = _try_douyin_f2(url, out_dir, cookies_file, provider, verbose,
                                   skip_existing=skip_existing)
        if f2_result:
            return f2_result

    chain = _resolve_cookies_chain(browser, cookies_file, provider=provider)

    # ── 1) 元数据（带 cookies 降级）─────────────────────────────────────
    print("\n[1/2] 🔍 抓取视频元数据...")
    info = None
    last_err: Optional[Exception] = None
    used_b: Optional[str] = None
    used_cf: Optional[str] = None

    from cookies_utils import ensure_netscape

    for idx, (b, cf, label) in enumerate(chain):
        if cf:
            try:
                cf = str(ensure_netscape(cf))
            except Exception as exc:
                last_err = RuntimeError(f"cookies 文件格式转换失败: {exc}")
                print(f"    ⚠️  跳过「{label}」：{exc}")
                continue
        print(f"    [yt-dlp] cookies 来源：{label}" + (f"（降级第 {idx} 次）" if idx else ""))
        base_args = _build_base_args(False, b, cf, provider=provider)
        try:
            info = _probe_metadata(url, base_args)
            used_b, used_cf = b, cf
            break
        except RuntimeError as exc:
            last_err = exc
            msg = str(exc)
            if _is_browser_decrypt_error(msg) and b:
                print("    ⚠️  浏览器 cookies 解密失败，换下一个来源...")
                continue
            if _is_auth_error(msg) and idx < len(chain) - 1:
                print("    ⚠️  认证失败，换下一个来源...")
                continue
            raise

    if info is None:
        raise RuntimeError(f"{last_err}{_cookies_failure_hint(provider)}") from last_err

    title = info.get("title", "video")
    video_id = info.get("id", "unknown")
    safe_title = _safe_filename(title)
    duration = info.get("duration", 0)
    print(f"    ✓ 标题: {title}")
    print(f"    ✓ 作者: {info.get('uploader', '')}")
    print(f"    ✓ 时长: {_fmt_duration(duration)}")

    prefix = f"{safe_title}_{video_id}"
    out_template = str(out_dir / f"{prefix}.%(ext)s")
    before = {p.resolve() for p in out_dir.glob(f"{prefix}*")}

    # ── 2) 下载 ────────────────────────────────────────────────────────
    fmt = build_format_selector(kind, quality, container)
    print(f"\n[2/2] 📥 开始下载（format={fmt}）...")

    base_args = _build_base_args(True, used_b, used_cf, provider=provider)
    dl_args = [*base_args, "-f", fmt, "-o", out_template, "--no-mtime"]

    if kind == "video" and container in ("mp4", "mkv"):
        dl_args += ["--merge-output-format", container]
    if kind == "audio":
        dl_args += ["-x"]
        if audio_format != "keep":
            dl_args += ["--audio-format", audio_format, "--audio-quality", "0"]
    if write_subs:
        dl_args += ["--write-subs", "--write-auto-subs",
                    "--sub-langs", sub_langs, "--convert-subs", "srt"]
    if write_thumbnail:
        dl_args += ["--write-thumbnail"]
    if embed_metadata:
        dl_args += ["--embed-metadata"]
    dl_args.append(url)

    result = _run_ytdlp(dl_args, capture=False)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败，退出码 {result.returncode}")

    # ── 3) 定位产物 ────────────────────────────────────────────────────
    produced = [p for p in out_dir.glob(f"{prefix}*")
                if p.is_file() and p.suffix.lower() not in (".part", ".ytdl", ".temp")]
    if not produced:
        raise FileNotFoundError(f"下载命令成功但在 {out_dir} 找不到产物文件")

    main_file = _pick_main_file(produced, kind)
    if main_file is None:
        raise FileNotFoundError("下载完成但未识别出主媒体文件")

    new_files = [p for p in produced if p.resolve() not in before]
    extras = [
        {"path": str(p), "kind": _classify(p), "bytes": p.stat().st_size}
        for p in sorted(produced)
        if p.resolve() != main_file.resolve()
    ]

    size = main_file.stat().st_size
    print(f"\n✅ 下载完成: {main_file}")
    print(f"    体积: {_fmt_bytes(size)}")
    if extras:
        print(f"    附带文件 {len(extras)} 个：" +
              "、".join(Path(e["path"]).name for e in extras[:6]))
    if not new_files:
        print("    （文件已存在，yt-dlp 跳过了重复下载）")

    return {
        "file_path": str(main_file),
        "file_size": size,
        "kind": _classify(main_file),
        "title": title,
        "uploader": info.get("uploader", ""),
        "duration": duration,
        "video_id": video_id,
        "url": url,
        "platform": provider.name,
        "platform_display": provider.display_name,
        "extra_files": extras,
    }


def _try_douyin_f2(
    url: str,
    out_dir: Path,
    cookies_file: Optional[str],
    provider: BaseProvider,
    verbose: bool,
    skip_existing: bool = True,
) -> Optional[dict]:
    """抖音 f2 增强通路：成功返回结果 dict，不可用返回 None（由调用方回退 yt-dlp）。"""
    try:
        from providers.douyin_f2 import (
            F2CookiesRequiredError,
            F2NotAvailableError,
            f2_download_video,
            f2_probe_metadata,
            is_available as f2_ok,
        )
    except ImportError:
        return None
    if not f2_ok():
        return None

    cookies_for_f2 = locate_cookies_file(provider, cookies_file)
    if not cookies_for_f2:
        return None

    try:
        print("    [f2] 尝试用 f2 增强通路下载抖音视频...")
        meta = f2_probe_metadata(url, cookies_file=cookies_for_f2, verbose=verbose)
        res = f2_download_video(url, meta, out_dir,
                                cookies_file=cookies_for_f2, verbose=verbose,
                                skip_existing=skip_existing)
        path = Path(res["audio_path"])   # f2 下载的就是完整 mp4（含音轨）
        size = path.stat().st_size if path.exists() else 0
        if res.get("skipped"):
            print(f"\n⏭  已存在，跳过: {path}    体积: {_fmt_bytes(size)}")
        else:
            print(f"\n✅ 下载完成: {path}\n    体积: {_fmt_bytes(size)}")
        return {
            "skipped": bool(res.get("skipped")),
            "file_path": str(path),
            "file_size": size,
            "kind": _classify(path),
            "title": res.get("title", ""),
            "uploader": res.get("uploader", ""),
            "duration": res.get("duration", 0),
            "video_id": res.get("video_id", ""),
            "url": url,
            "platform": provider.name,
            "platform_display": provider.display_name,
            "extra_files": [],
            "via": "f2",
        }
    except (F2NotAvailableError, F2CookiesRequiredError) as exc:
        print(f"    [f2] ⚠️  {exc}，回退 yt-dlp 通路")
    except Exception as exc:
        print(f"    [f2] ⚠️  f2 下载失败：{exc}\n           回退 yt-dlp 通路")
    return None


# ══════════════════════════════════════════════════════════════════════
# 列表型批量：把「个人主页 / 收藏 / 喜欢 / 收藏夹」展开成多条视频 URL
# ══════════════════════════════════════════════════════════════════════
class BatchExpandError(RuntimeError):
    """列表展开失败（登录态不足、依赖缺失、目标解析不出等）。"""

    def __init__(self, target: str, message: str, needs_login: bool = False):
        self.target = target
        self.message = message
        self.needs_login = needs_login
        super().__init__(message)


def _expand_one(
    provider: BaseProvider,
    batch_target,
    *,
    cookies_file: Optional[str],
    limit: int,
    include_images: bool,
    page_size: int,
    page_interval: int,
    verbose: bool,
) -> list[dict]:
    """展开单个批量目标，返回 [{url, title, uploader, aweme_id, ...}] 列表。"""
    from providers.douyin_f2 import F2CookiesRequiredError, F2NotAvailableError
    from providers.douyin_list import (
        DouyinListError,
        DouyinLoginExpiredError,
        DouyinSelfIdError,
        KIND_COLLECTS,
        KIND_LIKE,
        SELF_ONLY_KINDS,
        expand_target,
    )

    cookies = locate_cookies_file(provider, cookies_file)
    label = batch_target.label()

    if not cookies:
        raise BatchExpandError(
            batch_target.raw,
            f"抖音「{label}」批量下载需要登录态 cookies，但项目根目录没找到 "
            f"{provider.cookies_filename}。\n"
            "请先执行：python scripts/login_douyin.py 扫码登录。",
            needs_login=True,
        )

    if batch_target.kind == KIND_COLLECTS:
        arg = batch_target.collects_id
    elif batch_target.sec_user_id:
        arg = batch_target.sec_user_id
    else:
        arg = "self"

    print(f"\n📋 展开抖音「{label}」列表"
          f"（上限 {limit or '不限'}）...")
    if batch_target.kind in SELF_ONLY_KINDS:
        print("    ℹ️  该列表只能读取**本人**账号，依赖当前登录态")

    def _progress(count: int, note: str) -> None:
        print(f"    · 已抓取 {count} 条  {note}")

    try:
        items = expand_target(
            batch_target.kind, arg, cookies,
            limit=limit,
            page_size=page_size,
            page_interval=page_interval,
            include_images=include_images,
            on_progress=_progress,
            verbose=verbose,
        )
    except (F2NotAvailableError,) as exc:
        raise BatchExpandError(batch_target.raw, str(exc)) from exc
    except (F2CookiesRequiredError,) as exc:
        raise BatchExpandError(batch_target.raw, str(exc), needs_login=True) from exc
    except (DouyinLoginExpiredError, DouyinSelfIdError) as exc:
        # 登录态失效 / 认不出本人 → 归到 login_required，GUI 会引导去扫码
        raise BatchExpandError(batch_target.raw, str(exc), needs_login=True) from exc
    except DouyinListError as exc:
        raise BatchExpandError(batch_target.raw, str(exc)) from exc
    except Exception as exc:
        msg = str(exc)
        raise BatchExpandError(
            batch_target.raw,
            f"抖音「{label}」列表抓取失败：{msg}",
            needs_login=_looks_like_login_required(msg),
        ) from exc

    if not items:
        hint = ""
        if batch_target.kind == KIND_LIKE:
            hint = ("\n    抖音默认隐藏点赞列表：本人需在 APP「设置 → 隐私设置 → "
                    "点赞列表」里开放；他人账号未公开时无法读取。")
        elif batch_target.kind in SELF_ONLY_KINDS:
            hint = "\n    请确认 cookies 是本人账号且登录态未过期。"
        raise BatchExpandError(
            batch_target.raw,
            f"抖音「{label}」列表为空，没有可下载的视频。{hint}",
            needs_login=batch_target.kind in SELF_ONLY_KINDS,
        )

    print(f"    ✓ 共展开 {len(items)} 个视频")
    for i, it in enumerate(items[:5], 1):
        print(f"      {i}. {it.summary()}")
    if len(items) > 5:
        print(f"      … 其余 {len(items) - 5} 个略")

    folder = batch_folder_name(batch_target, [it.nickname for it in items])
    return [{
        "url": it.url,
        "aweme_id": it.aweme_id,
        "title": it.desc,
        "uploader": it.nickname,
        "create_time": it.create_time,
        "duration": it.duration,
        "batch_kind": batch_target.kind,
        "batch_label": label,
        "batch_folder": folder,
    } for it in items]


_ILLEGAL_PATH_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_folder_name(name: str, max_len: int = 60) -> str:
    """把任意文本变成 Windows / macOS 都合法的单级目录名。"""
    name = _ILLEGAL_PATH_CHARS.sub("_", str(name or "")).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].rstrip(" ._") or "未命名"


def batch_folder_name(batch_target, nicknames: list[str]) -> str:
    """每个批量任务的子目录名：稳定、可读，重复执行会落到同一目录（便于增量补全）。"""
    kind = batch_target.kind
    short_uid = (batch_target.sec_user_id or "")[-12:]
    if kind == "post":
        names = [n for n in nicknames if n]
        who = max(set(names), key=names.count) if names else short_uid
        return safe_folder_name(f"抖音_{who or '未知用户'}_主页作品")
    if kind == "like":
        if batch_target.is_self or not short_uid:
            return "抖音_本人喜欢"
        return safe_folder_name(f"抖音_{short_uid}_喜欢")
    if kind == "collection":
        return "抖音_本人收藏"
    if kind == "collects":
        return safe_folder_name(f"抖音_收藏夹_{batch_target.collects_id or '未知'}")
    return safe_folder_name(f"抖音_{kind}")


def expand_batch_inputs(
    raw_inputs: list[str],
    *,
    platform: Optional[str] = None,
    cookies_file: Optional[str] = None,
    limit: int = 0,
    include_images: bool = False,
    page_size: int = 20,
    page_interval: int = 8,
    enabled: bool = True,
    verbose: bool = True,
) -> tuple[list[str], list[dict], list[dict]]:
    """把输入里的聚合页入口展开成具体视频 URL。

    返回 (urls, listing, errors)：
        urls    —— 最终要下载的 URL 列表（普通链接原样保留，顺序稳定且已去重）
        listing —— 展开出来的条目明细（带标题/作者，供 --list-only 和 GUI 展示）
        errors  —— 展开失败的目标（结构同 main() 的 errors）
    """
    urls: list[str] = []
    listing: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()

    def _add(u: str) -> None:
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    for raw in raw_inputs:
        raw = (raw or "").strip()
        if not raw:
            continue
        if enabled:
            # 裸 sec_uid / self / collection 等简写补成完整抖音 URL
            raw = normalize_douyin_batch_input(raw)

        provider: Optional[BaseProvider] = None
        try:
            provider = resolve_provider(raw, platform=platform)
        except Exception:
            provider = None

        if not enabled or provider is None or not provider.supports_batch:
            _add(raw)
            continue

        try:
            batch_target = provider.parse_batch_target(raw)
        except Exception:
            batch_target = None

        if not batch_target:
            _add(raw)                       # 普通单视频链接
            continue

        try:
            entries = _expand_one(
                provider, batch_target,
                cookies_file=cookies_file,
                limit=limit,
                include_images=include_images,
                page_size=page_size,
                page_interval=page_interval,
                verbose=verbose,
            )
        except BatchExpandError as exc:
            print(f"\n❌ 批量展开失败: {raw}\n   {exc.message}")
            if exc.needs_login:
                errors.append(_build_login_error(
                    raw, "douyin", exc.message))
            else:
                errors.append({"url": raw, "error": exc.message,
                               "error_type": "batch_expand_failed"})
            continue

        for e in entries:
            if e["url"] not in seen:
                listing.append(e)
            _add(e["url"])

    return urls, listing, errors


DOUYIN_SELF_COLLECTION_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"
DOUYIN_SELF_LIKE_URL = "https://www.douyin.com/user/self?showTab=like"


def normalize_douyin_batch_input(raw: str) -> str:
    """把简写形式补成完整抖音 URL，好让 resolve_provider 认出是抖音。

    裸 sec_uid / "self" / "collection" / "like" 这些简写虽然
    douyin_list.parse_batch_target 认得，但 resolve_provider 只按域名匹配，
    不补全就会被当成未知平台交给 yt-dlp。
    """
    raw = (raw or "").strip().strip('"')
    if not raw or "douyin.com" in raw.lower():
        return raw

    low = raw.lower()
    if low in ("collection", "favorite", "favorites", "收藏"):
        return DOUYIN_SELF_COLLECTION_URL
    if low in ("like", "likes", "喜欢", "点赞"):
        return DOUYIN_SELF_LIKE_URL

    from providers.douyin_list import extract_sec_user_id, is_self_target

    if is_self_target(raw):
        return "https://www.douyin.com/user/self"
    # 纯 sec_uid（不含协议/域名）→ 补成主页链接
    if "://" not in raw and "/" not in raw:
        sec_uid = extract_sec_user_id(raw)
        if sec_uid:
            return f"https://www.douyin.com/user/{sec_uid}"
    return raw


def _as_like_target(user: str) -> str:
    """把「用户主页 URL / 裸 sec_uid / 空」转成指向「喜欢列表」的抖音 URL。"""
    user = (user or "").strip()
    from providers.douyin_list import extract_sec_user_id, is_self_target

    if not user or is_self_target(user):
        return DOUYIN_SELF_LIKE_URL         # 本人
    sec_uid = extract_sec_user_id(user)
    if sec_uid:
        return f"https://www.douyin.com/user/{sec_uid}?showTab=like"
    # 解析不出 sec_uid 时原样传，让下游报更准的错
    return user


def build_batch_inputs(args) -> list[str]:
    """把 --douyin-* 系列显式参数翻译成可被自动展开识别的抖音 URL。"""
    out: list[str] = []
    user = (getattr(args, "douyin_user", None) or "").strip()
    want_likes = bool(getattr(args, "douyin_likes", False))
    want_collection = bool(getattr(args, "douyin_collection", False))
    collects = (getattr(args, "douyin_collects", None) or "")

    if want_collection:
        out.append(DOUYIN_SELF_COLLECTION_URL)
    if collects:
        out.append(f"https://www.douyin.com/collection/{str(collects).strip()}")
    if want_likes:
        out.append(_as_like_target(user))
    if user and not want_likes:
        out.append(normalize_douyin_batch_input(user))   # 主页作品
    return out


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════
def _build_login_error(url: str, platform: str, reason: str, original: str = "") -> dict:
    script = {"bilibili": "scripts/login_bilibili.py",
              "douyin": "scripts/login_douyin.py"}.get(platform, "")
    return {
        "url": url,
        "error_type": "login_required",
        "platform": platform,
        "error": f"[{platform}] 需要登录：{reason}",
        "reason": reason,
        "original_error": original,
        "install_guide": {
            "step1_install_deps": "pip install -r requirements-login.txt",
            "step2_install_browser": "python -m playwright install chromium",
            "step3_run_login": f"python {script}" if script else "",
            "cookies_output_file": f"{platform}_cookies.json",
            "retry_after_login": "登录完成后重跑下载即可",
        },
    }


def _print_listing(urls: list[str], listing: list[dict]) -> None:
    """--list-only：把展开结果打印成人类可读的清单。"""
    by_url = {e["url"]: e for e in listing}
    print(f"\n{'='*60}")
    print(f"📋 共 {len(urls)} 个视频（未下载）")
    print(f"{'='*60}")
    for i, u in enumerate(urls, 1):
        e = by_url.get(u)
        if e:
            title = (e.get("title") or "").replace("\n", " ")[:50]
            who = e.get("uploader") or ""
            when = e.get("create_time") or ""
            print(f"  {i:>4}. {u}")
            print(f"        {('@' + who + '  ') if who else ''}{when}  {title}")
        else:
            print(f"  {i:>4}. {u}")


def _print_collects_folders(args) -> None:
    """--douyin-list-collects：列出本人的收藏夹及 ID。"""
    provider = PROVIDERS_BY_NAME.get("douyin")
    cookies = locate_cookies_file(provider, args.cookies_file)
    if not cookies:
        print("❌ 没找到 douyin_cookies.json，请先执行：python scripts/login_douyin.py")
        sys.exit(2)
    try:
        from providers.douyin_list import list_collects_folders

        folders = list_collects_folders(
            cookies,
            page_size=max(1, args.page_size),
            page_interval=max(0, args.page_interval),
            verbose=True,
        )
    except Exception as exc:
        print(f"❌ 获取收藏夹列表失败：{exc}")
        sys.exit(2)

    if not folders:
        print("ℹ️  当前账号没有收藏夹（或登录态已失效）。")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"📂 本人收藏夹共 {len(folders)} 个")
    print(f"{'='*60}")
    for f in folders:
        print(f"  · {f.name or '(未命名)'}   共 {f.total} 个作品")
        print(f"    ID: {f.collects_id}")
    print("\n下载某个收藏夹：")
    print(f"  python media_downloader.py --douyin-collects {folders[0].collects_id} -o ./videos")
    if args.json:
        print("\n===JSON_RESULT_BEGIN===")
        print(json.dumps({"success": [], "errors": [], "collects": [
            {"collects_id": f.collects_id, "name": f.name, "total": f.total}
            for f in folders
        ]}, ensure_ascii=False))
        print("===JSON_RESULT_END===")
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只下载视频/音频，不做转写（多平台：YouTube / B 站 / 抖音）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
抖音批量示例：
  # 1) 任意对方个人主页批量下载（也可直接把主页链接当普通 URL 传）
  python media_downloader.py --douyin-user https://www.douyin.com/user/MS4wLjABAAAA... -o ./videos
  python media_downloader.py https://www.douyin.com/user/MS4wLjABAAAA... -n 30

  # 2) 本人「收藏」批量下载
  python media_downloader.py --douyin-collection -o ./videos

  # 2) 本人「喜欢（点赞）」批量下载
  python media_downloader.py --douyin-likes -o ./videos

  # 指定收藏夹（先列出 ID）
  python media_downloader.py --douyin-list-collects
  python media_downloader.py --douyin-collects 7123456789 -o ./videos

  # 先看清单不下载
  python media_downloader.py --douyin-collection --list-only

  # 默认每个批量任务存进 -o 下的独立子目录（如 抖音_本人收藏/），可自定义或关闭
  python media_downloader.py --douyin-collects 7123456789 --batch-folder 抖音_收藏夹_美食
  python media_downloader.py --douyin-collection --no-batch-subdir
""",
    )
    parser.add_argument("url", nargs="?", help="视频 URL")
    parser.add_argument("--file", "-f", help="从文件批量读取 URL（每行一个）")
    parser.add_argument("--output-dir", "-o", default="./videos", help="下载输出目录")
    parser.add_argument("--kind", default="video", choices=["video", "audio"],
                        help="video=下载视频（默认） | audio=只下载音频")
    parser.add_argument("--quality", "-q", default="best", choices=QUALITY_CHOICES,
                        help="视频最高分辨率，best=不限（默认）")
    parser.add_argument("--container", default="mp4", choices=CONTAINER_CHOICES,
                        help="视频封装格式：mp4（默认，兼容性最好）/ mkv / keep=保持原始")
    parser.add_argument("--audio-format", default="keep", choices=AUDIO_FORMAT_CHOICES,
                        help="--kind audio 时的音频格式，keep=保持原始（默认）")
    parser.add_argument("--subs", action="store_true", help="同时下载字幕文件（转为 srt）")
    parser.add_argument("--sub-langs", default=DEFAULT_SUB_LANGS,
                        help=f"字幕语言列表，默认 {DEFAULT_SUB_LANGS}")
    parser.add_argument("--thumbnail", action="store_true", help="同时下载封面图")
    parser.add_argument("--embed-metadata", action="store_true", help="把标题/作者写入文件元数据")
    parser.add_argument("--platform", default=None, choices=sorted(PROVIDERS_BY_NAME.keys()),
                        help="强制指定平台（默认从 URL 自动识别）")
    parser.add_argument("--browser", "-b", default=None,
                        help="从哪个浏览器读 cookies：chrome/edge/firefox…，传 none 禁用")
    parser.add_argument("--cookies-file", default=None, help="指定 cookies 文件（优先级最高）")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 结果（供 GUI 解析）")

    # ── 抖音列表型批量 ────────────────────────────────────────────────
    dy = parser.add_argument_group(
        "抖音批量下载（需要 f2 + 扫码登录 cookies）",
        "把「个人主页 / 收藏 / 喜欢 / 收藏夹」展开成多个视频后逐个下载。\n"
        "也可以直接把主页链接当普通 URL 传入，会自动识别并展开。",
    )
    dy.add_argument("--douyin-user", default=None, metavar="URL|SEC_UID",
                    help="批量下载该用户主页的全部作品。可传主页链接、裸 sec_uid，"
                         "或 self 表示本人")
    dy.add_argument("--douyin-likes", action="store_true",
                    help="批量下载「喜欢（点赞）」列表；不配 --douyin-user 时取本人。"
                         "注意抖音默认隐藏点赞列表，本人需在 APP 里开放")
    dy.add_argument("--douyin-collection", action="store_true",
                    help="批量下载**本人**的「收藏」列表（只能是本人）")
    dy.add_argument("--douyin-collects", default=None, metavar="ID",
                    help="批量下载**本人**指定收藏夹里的作品（收藏夹 ID）")
    dy.add_argument("--douyin-list-collects", action="store_true",
                    help="只列出本人的所有收藏夹及其 ID，然后退出")
    dy.add_argument("--limit", "-n", type=int, default=0, metavar="N",
                    help="每个批量目标最多取 N 个视频，0=不限（默认）")
    dy.add_argument("--include-images", action="store_true",
                    help="保留图文/图集作品（默认剔除，因为下载器只处理视频）")
    dy.add_argument("--page-size", type=int, default=20, metavar="N",
                    help="列表接口每页条数，默认 20")
    dy.add_argument("--page-interval", type=int, default=8, metavar="SEC",
                    help="翻页间隔秒数，默认 8（调小容易触发风控）")
    dy.add_argument("--no-expand", action="store_true",
                    help="禁用聚合页自动展开，把主页链接当普通链接交给 yt-dlp")
    dy.add_argument("--list-only", action="store_true",
                    help="只展开并列出视频清单，不实际下载")
    dy.add_argument("--overwrite", action="store_true",
                    help="即使本地已有同名文件也重新下载（默认跳过，便于增量补全）")
    dy.add_argument("--no-batch-subdir", action="store_true",
                    help="批量展开的视频直接放进输出目录（默认每个批量任务单独建子目录，"
                         "如 抖音_某某_主页作品 / 抖音_本人收藏）")
    dy.add_argument("--batch-folder", default=None, metavar="NAME",
                    help="自定义批量子目录名（覆盖自动命名）")

    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    browser = "" if args.browser == "none" else args.browser

    # ── 只列收藏夹：单独的短路分支 ────────────────────────────────────
    if args.douyin_list_collects:
        _print_collects_folders(args)
        return

    raw_inputs: list[str] = []
    if args.url:
        raw_inputs.append(args.url.strip())
    if args.file:
        # utf-8-sig：记事本 / PowerShell 写出的清单常带 BOM，
        # 否则首行会变成 "\ufeff#..." 导致注释判断失效、被当成 URL
        with open(args.file, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip().strip('"')
                if line and not line.startswith("#"):
                    raw_inputs.append(line)
    raw_inputs.extend(build_batch_inputs(args))

    if not raw_inputs:
        parser.print_help()
        sys.exit(1)

    results: list[dict] = []
    errors: list[dict] = []

    # ── 聚合页展开 ────────────────────────────────────────────────────
    urls, listing, expand_errors = expand_batch_inputs(
        raw_inputs,
        platform=args.platform,
        cookies_file=args.cookies_file,
        limit=args.limit,
        include_images=args.include_images,
        page_size=max(1, args.page_size),
        page_interval=max(0, args.page_interval),
        enabled=not args.no_expand,
    )
    errors.extend(expand_errors)

    use_subdir = not args.no_batch_subdir
    custom_folder = safe_folder_name(args.batch_folder) if (args.batch_folder or "").strip() else ""
    for e in listing:
        if not use_subdir:
            e["batch_folder"] = ""
        elif custom_folder:
            e["batch_folder"] = custom_folder
    batch_dirs = sorted({e["batch_folder"] for e in listing if e.get("batch_folder")})
    if batch_dirs:
        print("\n📂 批量任务将分别保存到子目录：")
        for d in batch_dirs:
            print(f"    · {out_dir / d}")

    if args.list_only:
        _print_listing(urls, listing)
        if args.json:
            print("\n===JSON_RESULT_BEGIN===")
            print(json.dumps({"success": [], "errors": errors,
                              "listing": listing, "urls": urls,
                              "batch_dirs": [str(out_dir / d) for d in batch_dirs]},
                             ensure_ascii=False))
            print("===JSON_RESULT_END===")
        sys.exit(0 if not errors else 2)

    if not urls:
        print("\n⚠️  没有可下载的链接。")
        if args.json:
            print("\n===JSON_RESULT_BEGIN===")
            print(json.dumps({"success": [], "errors": errors,
                              "listing": listing}, ensure_ascii=False))
            print("===JSON_RESULT_END===")
        sys.exit(2)

    batch_meta = {e["url"]: e for e in listing}

    for i, url in enumerate(urls, 1):
        if len(urls) > 1:
            print(f"\n\n########## 进度 {i}/{len(urls)} ##########")
        meta = batch_meta.get(url)
        sub = (meta or {}).get("batch_folder") or ""
        target_dir = out_dir / sub if sub else out_dir
        try:
            res = download_media(
                url=url,
                out_dir=target_dir,
                kind=args.kind,
                quality=args.quality,
                container=args.container,
                audio_format=args.audio_format,
                write_subs=args.subs,
                sub_langs=args.sub_langs,
                write_thumbnail=args.thumbnail,
                embed_metadata=args.embed_metadata,
                browser=browser,
                cookies_file=args.cookies_file,
                platform=args.platform,
                skip_existing=not args.overwrite,
            )
            if meta:
                # 记录这条是从哪个聚合页展开来的，便于 GUI 分组展示
                res["batch_kind"] = meta.get("batch_kind", "")
                res["batch_label"] = meta.get("batch_label", "")
                res["batch_dir"] = str(target_dir)
                if not res.get("title"):
                    res["title"] = meta.get("title", "")
                if not res.get("uploader"):
                    res["uploader"] = meta.get("uploader", "")
            results.append(res)
        except Exception as exc:
            msg = str(exc)
            plat = args.platform
            if not plat:
                try:
                    plat = resolve_provider(url).name
                except Exception:
                    plat = ""
            if plat in ("bilibili", "douyin") and _looks_like_login_required(msg):
                print(f"\n⚠️ 疑似 {plat} 登录态失效/缺失: {url}")
                errors.append(_build_login_error(
                    url, plat,
                    "下载失败且错误信息匹配登录失效特征（HTTP 403 / 未登录 等）", msg))
            else:
                print(f"\n❌ 下载失败: {url}\n   {msg}")
                traceback.print_exc()
                errors.append({"url": url, "error": msg})

    skipped = sum(1 for r in results if r.get("skipped"))
    print(f"\n\n{'='*60}")
    print(f"🎉 全部完成！成功 {len(results)} / 失败 {len(errors)}"
          + (f"（其中 {skipped} 个已存在被跳过）" if skipped else ""))
    print(f"{'='*60}")
    total = 0
    for r in results:
        total += r.get("file_size", 0)
        mark = "⏭" if r.get("skipped") else "✓"
        print(f"  {mark} {r['title']}")
        print(f"    → {r['file_path']}  ({_fmt_bytes(r.get('file_size', 0))})")
    if results:
        print(f"\n  合计 {_fmt_bytes(total)}，输出目录：{out_dir}")

    if args.json:
        print("\n===JSON_RESULT_BEGIN===")
        payload: dict = {"success": results, "errors": errors}
        if listing:
            payload["listing"] = listing
        if batch_dirs:
            payload["batch_dirs"] = [str(out_dir / d) for d in batch_dirs]
        print(json.dumps(payload, ensure_ascii=False))
        print("===JSON_RESULT_END===")

    sys.exit(0 if not errors else 2)


if __name__ == "__main__":
    main()
