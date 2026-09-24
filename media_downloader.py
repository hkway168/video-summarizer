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
) -> dict:
    """把一个 URL 的视频/音频下载到 out_dir，不做任何转写。

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
        f2_result = _try_douyin_f2(url, out_dir, cookies_file, provider, verbose)
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

    cookies_for_f2 = cookies_file
    if not cookies_for_f2:
        script_dir = Path(__file__).parent
        for candidate in _cookies_candidates_for(provider):
            p = script_dir / candidate
            if p.exists() and _cookie_file_has_content(p):
                cookies_for_f2 = str(p)
                break
    if not cookies_for_f2:
        return None

    try:
        print("    [f2] 尝试用 f2 增强通路下载抖音视频...")
        meta = f2_probe_metadata(url, cookies_file=cookies_for_f2, verbose=verbose)
        res = f2_download_video(url, meta, out_dir,
                                cookies_file=cookies_for_f2, verbose=verbose)
        path = Path(res["audio_path"])   # f2 下载的就是完整 mp4（含音轨）
        size = path.stat().st_size if path.exists() else 0
        print(f"\n✅ 下载完成: {path}\n    体积: {_fmt_bytes(size)}")
        return {
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只下载视频/音频，不做转写（多平台：YouTube / B 站 / 抖音）",
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

    args = parser.parse_args()

    urls: list[str] = []
    if args.url:
        urls.append(args.url.strip())
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    if not urls:
        parser.print_help()
        sys.exit(1)

    out_dir = Path(args.output_dir).resolve()
    browser = "" if args.browser == "none" else args.browser

    results: list[dict] = []
    errors: list[dict] = []

    for i, url in enumerate(urls, 1):
        if len(urls) > 1:
            print(f"\n\n########## 进度 {i}/{len(urls)} ##########")
        try:
            results.append(download_media(
                url=url,
                out_dir=out_dir,
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
            ))
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

    print(f"\n\n{'='*60}")
    print(f"🎉 全部完成！成功 {len(results)} / 失败 {len(errors)}")
    print(f"{'='*60}")
    total = 0
    for r in results:
        total += r.get("file_size", 0)
        print(f"  ✓ {r['title']}")
        print(f"    → {r['file_path']}  ({_fmt_bytes(r.get('file_size', 0))})")
    if results:
        print(f"\n  合计 {_fmt_bytes(total)}，输出目录：{out_dir}")

    if args.json:
        print("\n===JSON_RESULT_BEGIN===")
        print(json.dumps({"success": results, "errors": errors}, ensure_ascii=False))
        print("===JSON_RESULT_END===")

    sys.exit(0 if not errors else 2)


if __name__ == "__main__":
    main()
