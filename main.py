"""
main.py —— 视频转写主入口（多平台：YouTube / B 站 / 抖音 / ...）
作用：
    1. 根据 URL 自动识别平台（或用 --platform 显式指定）
    2. 优先抓原生/自动字幕，没字幕时回退本地 Whisper (GPU) 转写
    3. 输出 transcript.md 文件，包含：
         - 视频元信息（标题、作者、时长、URL）
         - 文字稿
         - Whisper 模式时会附带带时间戳的版本

注意：本脚本【不做总结】。总结工作交给调用它的 Agent (Claude) 完成，
      这样可以获得更好的质量和更灵活的风格控制。

用法：
    python main.py <URL>                               # 自动识别平台
    python main.py <URL> --platform bilibili           # 强制指定平台
    python main.py <URL> --model medium                # 显式指定 Whisper 模型
    python main.py --file urls.txt                     # 批量处理
    python main.py --list-models                       # 查看本地已安装的模型
"""
from __future__ import annotations
import argparse
import io
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# 强制 stdout/stderr 使用 UTF-8，避免 Windows GBK 环境下 emoji 报错
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from downloader import download_audio, probe_metadata_only, _safe_filename
from subtitle_fetcher import fetch_subtitle, PREFERRED_LANGS
from providers import resolve_provider, ALL_PROVIDERS, PROVIDERS_BY_NAME, BaseProvider
# 👇 transcriber 的导入延迟到真正需要 Whisper 时再执行，避免没装 faster-whisper 时
#    仅走字幕通路的用户被 ImportError 挡在外面。


class WhisperNotReadyError(RuntimeError):
    """当字幕失败需要回退 Whisper、但 Whisper 环境未就绪时抛出。

    会在 main() 里被捕获，转换为结构化 JSON 错误（error_type=whisper_required），
    让上层 Agent（Skill）能读到清晰的安装指引并转述给用户。
    """
    def __init__(self, missing: list[str], suggested_size: str = "medium"):
        self.missing = missing
        self.suggested_size = suggested_size
        super().__init__(
            "字幕获取失败需要回退 Whisper 转写，但 Whisper 环境未就绪：\n  - "
            + "\n  - ".join(missing)
        )


class LoginRequiredError(RuntimeError):
    """B 站 / 抖音等平台缺少有效登录 cookies 导致下载失败时抛出。

    会在 main() 里被捕获，转换为结构化 JSON 错误（error_type=login_required），
    上层 Agent 据此引导用户运行 `scripts/login_<platform>.py` 扫码登录。
    """
    def __init__(self, platform: str, reason: str, original_error: str = ""):
        self.platform = platform
        self.reason = reason
        self.original_error = original_error
        super().__init__(f"[{platform}] 需要登录：{reason}")


# 平台 → 扫码登录脚本（按需依赖）
_LOGIN_SCRIPTS: dict[str, str] = {
    "bilibili": "scripts/login_bilibili.py",
    "douyin":   "scripts/login_douyin.py",
}

# 常见「需要登录」的错误特征，用于从下载异常消息里做启发式识别
_LOGIN_HINT_PATTERNS: tuple[str, ...] = (
    "http error 403",
    "http error 412",
    "login required",
    "需要登录",
    "需登录",
    "请登录",
    "please login",
    "sign in",
    "cookies are no longer valid",
    "account_logout",
    "invalid cookies",
    "fresh cookies",   # 抖音 extractor：Fresh cookies (not necessarily logged in) are needed
    "-352",        # B 站风控返回码
    "-101",        # B 站"账号未登录"返回码
    "verify_captcha",
)


def _looks_like_login_required(err_msg: str) -> bool:
    """启发式判断一条异常消息是否是"登录态失效/缺失"引起的。"""
    if not err_msg:
        return False
    low = err_msg.lower()
    return any(p in low for p in _LOGIN_HINT_PATTERNS)


def _infer_platform_from_url(url: str, explicit: Optional[str] = None) -> Optional[str]:
    """用 providers 注册表识别 URL 所属平台；失败返回 None（不抛异常）。"""
    if explicit:
        return explicit
    try:
        p = resolve_provider(url, platform=None)
        return p.name
    except Exception:
        return None


def _build_login_required_entry(
    *,
    url: str,
    platform: str,
    reason: str,
    original_error: str = "",
) -> dict:
    """构造 error_type=login_required 的结构化条目，供 Skill/Agent 解析。"""
    script = _LOGIN_SCRIPTS.get(platform, "")
    cookies_file = f"{platform}_cookies.json"
    install_guide: dict = {
        "step1_install_deps": "pip install -r requirements-login.txt",
        "step2_install_browser": "python -m playwright install chromium",
        "step3_run_login": f"python {script}" if script else "",
        "cookies_output_file": cookies_file,
        "manual_fallback": (
            "如不想装 Playwright，也可继续沿用 J2TEAM Cookies 扩展"
            f"手动导出 JSON 覆盖 {cookies_file}。"
        ),
        "retry_after_login": "登录完成后重跑：python main.py <URL> --json",
        "cookies_lifetime_hint": {
            "bilibili": "B 站 cookies 有效期约 30 天",
            "douyin":   "抖音 cookies 有效期约 15~30 天",
        }.get(platform, ""),
    }
    # 抖音专属：首选 f2 增强下载器，能绕过 yt-dlp 当前无法应对的风控
    if platform == "douyin":
        install_guide["preferred_path_note"] = (
            "⭐ 抖音推荐优先安装 f2 增强下载器（绕过 yt-dlp 当前无法应对的风控）。"
            "安装后仍需要 douyin_cookies.json（可通过扫码登录脚本或 J2TEAM Cookies 扩展获得）。"
        )
        install_guide["install_f2"] = "pip install -r requirements-douyin.txt"
        install_guide["retry_after_f2"] = "安装完成后重跑：python main.py <URL> --json"
    return {
        "url": url,
        "error_type": "login_required",
        "platform": platform,
        "error": f"[{platform}] 需要登录：{reason}",
        "reason": reason,
        "original_error": original_error,
        "install_guide": install_guide,
    }


# 模型质量优先级（高 → 低），用于自动选择本地最优模型
MODEL_QUALITY_RANK: list[str] = ["large-v3", "large-v2", "medium", "small", "base", "tiny"]
MODELS_DIR_NAME = "models"


def _models_root() -> Path:
    return Path(__file__).parent / MODELS_DIR_NAME


def _is_valid_model(model_size: str) -> bool:
    """判断 models/<size>/model.bin 是否存在且大小合理（认为 ≥ 10MB 算有效）。"""
    model_bin = _models_root() / model_size / "model.bin"
    try:
        return model_bin.exists() and model_bin.stat().st_size >= 10 * 1024 * 1024
    except OSError:
        return False


def _list_local_models() -> list[dict]:
    """扫描 models/ 目录，返回所有已安装的模型信息。

    按 MODEL_QUALITY_RANK 从高到低排序。每个元素：
        {"size": "medium", "path": "...", "bytes": 1523000000}
    """
    root = _models_root()
    if not root.exists():
        return []
    found: list[dict] = []
    for size in MODEL_QUALITY_RANK:
        if _is_valid_model(size):
            model_bin = root / size / "model.bin"
            found.append({
                "size": size,
                "path": str(root / size),
                "bytes": model_bin.stat().st_size,
            })
    return found


def _pick_best_local_model() -> Optional[str]:
    """按质量优先级返回本地最优的模型名称，没有则返回 None。"""
    models = _list_local_models()
    return models[0]["size"] if models else None


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.2f} PB"


def _print_local_models() -> None:
    """打印本地已安装/未安装的 Whisper 模型清单（用于 --list-models）。"""
    root = _models_root()
    installed = {m["size"]: m for m in _list_local_models()}
    print(f"\n📦 Whisper 模型目录: {root}")
    print(f"{'─'*60}")
    if installed:
        print("✅ 已安装：")
        for size in MODEL_QUALITY_RANK:
            if size in installed:
                m = installed[size]
                print(f"   - {size:<10} {_fmt_bytes(m['bytes']):>10}   {m['path']}")
    else:
        print("⚠️  本地尚未安装任何 Whisper 模型")

    not_installed = [s for s in MODEL_QUALITY_RANK if s not in installed]
    if not_installed:
        print("\n❌ 未安装：")
        size_hint = {
            "tiny": "~75 MB", "base": "~145 MB", "small": "~460 MB",
            "medium": "~1.4 GB", "large-v2": "~3.0 GB", "large-v3": "~3.0 GB",
        }
        for size in not_installed:
            print(f"   - {size:<10} {size_hint.get(size, ''):>10}   python scripts/download_model.py --size {size}")

    best = _pick_best_local_model()
    print(f"{'─'*60}")
    if best:
        print(f"🎯 不指定 --model 时将自动使用: {best}")
    else:
        print("💡 运行转写前请先下载至少一个模型（推荐 medium）")


def _check_whisper_ready(model_size: str = "medium") -> tuple[bool, list[str]]:
    """检查 Whisper 通路是否可用。

    返回 (ready, missing_items)。missing_items 为缺失项的人类可读描述。
    """
    missing: list[str] = []

    # 1) faster-whisper 库
    try:
        import importlib
        importlib.import_module("faster_whisper")
    except ImportError:
        missing.append("faster-whisper 库未安装")

    # 2) 本地模型目录（含有效的 model.bin）
    if not _is_valid_model(model_size):
        # 如果本地其实有别的模型，在提示里直接告知用户
        available = [m["size"] for m in _list_local_models()]
        if available:
            missing.append(
                f"本地未找到模型 '{model_size}'（期望位置: models/{model_size}/model.bin）\n"
                f"       但你本地已有：{', '.join(available)}，可改用 --model {available[0]}"
            )
        else:
            missing.append(f"本地 Whisper 模型未下载（期望位置: models/{model_size}/model.bin）")

    return (not missing), missing


def _fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}小时{m}分{s}秒"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _pick_preferred_lang_from_meta(meta: dict, preferred: list[str]) -> tuple[Optional[str], Optional[str]]:
    """
    从元数据的 subtitles/automatic_captions 里挑一个最合适的语言。
    返回 (lang, source) 或 (None, None)。source ∈ {'manual', 'auto'}。
    自动过滤 live_chat / rechat 等伪字幕轨道（这些在 yt-dlp 的 subtitles 字段里
    但不是真正的字幕，选中后下载不出 vtt 文件）。
    """
    # 延迟导入以复用 subtitle_fetcher 的过滤器，保持两处一致
    from subtitle_fetcher import filter_real_subs

    subs = filter_real_subs(meta.get("subtitles"))
    autos = filter_real_subs(meta.get("automatic_captions"))

    # 先找原生（作者上传）
    for lang in preferred:
        if lang in subs:
            return lang, "manual"
    # 前缀匹配原生
    for lang in preferred:
        for k in subs.keys():
            if k == lang or k.startswith(lang + "-"):
                return k, "manual"
    # 如果原生有任意语言，取第一个
    if subs:
        return next(iter(subs.keys())), "manual"
    # 回退自动字幕
    for lang in preferred:
        if lang in autos:
            return lang, "auto"
    for lang in preferred:
        for k in autos.keys():
            if k == lang or k.startswith(lang + "-"):
                return k, "auto"
    if autos:
        return next(iter(autos.keys())), "auto"
    return None, None


def process_one(
    url: str,
    output_dir: Path,
    download_dir: Path,
    model_size: str = "medium",
    keep_audio: bool = False,
    language: str | None = None,
    browser: str | None = None,
    cookies_file: str | None = None,
    mode: str = "auto",         # auto | subtitle-only | whisper-only
    sub_lang: str | None = None,   # 用户指定字幕语言，如 "zh-Hans"
    allow_auto_sub: bool = True,   # 是否允许回退到 YouTube 自动字幕
    platform: str | None = None,   # 强制指定平台
) -> dict:
    """
    处理一个 URL：元数据 → 优先字幕，失败回退 Whisper → 写 transcript.md

    mode:
        auto:           先试字幕，失败回退 Whisper（默认）
        subtitle-only:  只用字幕，没字幕就失败
        whisper-only:   跳过字幕，直接 Whisper 转写
    """
    # ── 平台识别 ────────────────────────────────────────────────────────
    provider: BaseProvider = resolve_provider(url, platform=platform)

    # 让各 Provider 有机会把平台特有的 URL 变体改写为 yt-dlp 能识别的形态
    # （例如抖音的 /jingxuan?modal_id=XXX → /video/XXX）
    normalized_url = provider.normalize_url(url)
    if normalized_url != url:
        print(f"🔀 URL 已归一化: {url}\n              → {normalized_url}")
        url = normalized_url

    output_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"🎬 开始处理: {url}")
    print(f"🌐 平台: {provider.display_name} ({provider.name})")
    print(f"🔧 模式: {mode}")
    print(f"{'='*60}")

    # ===== 1) 先抓元数据（轻量，秒级）=====
    print("\n[1/3] 🔍 抓取视频元数据...")
    meta = probe_metadata_only(
        url,
        verbose=False,
        browser=browser,
        cookies_file=cookies_file,
        provider=provider,
    )
    print(f"    ✓ 标题: {meta['title']}")
    print(f"    ✓ 作者: {meta['uploader']}")
    print(f"    ✓ 时长: {_fmt_duration(meta['duration'])}")

    n_manual = len(meta.get("subtitles") or {})
    n_auto = len(meta.get("automatic_captions") or {})
    print(f"    ✓ 字幕: 原生={n_manual}种 / 自动={n_auto}种")

    # 用转换后的 cookies 路径继续后续操作
    effective_cookies = meta.get("_cookies_file") or cookies_file
    effective_browser = meta.get("_browser") if not cookies_file else None

    # ===== 2) 决策：走字幕还是 Whisper =====
    transcript_text: str | None = None
    timed_text: str = "(字幕模式不提供逐句时间戳)"
    source_info: dict = {}
    source_type: str = "whisper"
    detected_lang: str = "unknown"
    elapsed: float = 0.0

    if mode != "whisper-only":
        print("\n[2/3] 📝 尝试抓取字幕...")
        # 按 provider 默认语言优先级，并补上通用 fallback
        plat_langs = list(provider.default_sub_langs) or list(PREFERRED_LANGS)
        prefer_order = [sub_lang] if sub_lang else []
        prefer_order += [l for l in plat_langs if l not in prefer_order]
        prefer_order += [l for l in PREFERRED_LANGS if l not in prefer_order]

        # 本地挑选逻辑更灵活，直接传 prefer_lang 给 fetch_subtitle
        lang_guess, src_guess = _pick_preferred_lang_from_meta(meta, prefer_order)
        if lang_guess:
            print(f"    [字幕] 元数据预判最佳: {lang_guess} ({src_guess})")

        sub_result = fetch_subtitle(
            url=url,
            out_dir=download_dir,
            video_id=meta["video_id"],
            safe_title=meta["safe_title"],
            cookies_file=effective_cookies,
            browser=effective_browser,
            prefer_lang=sub_lang or lang_guess,
            allow_auto=allow_auto_sub,
            verbose=True,
            provider=provider,
        )

        if sub_result:
            transcript_text = sub_result["text"]
            detected_lang = sub_result["lang"]
            source_info = {
                "type": "subtitle",
                "subtitle_source": sub_result["source"],  # manual | auto
                "vtt_path": sub_result["vtt_path"],
                "platform": provider.name,
            }
            source_type = "subtitle"

    # ===== 3) 没拿到字幕则走 Whisper =====
    if transcript_text is None:
        if mode == "subtitle-only":
            raise RuntimeError(
                "字幕模式启用 (subtitle-only)，但该视频没有可用字幕。\n"
                "请改用 --mode auto 让它回退 Whisper 转写。"
            )

        # 👇 在下载音频和加载模型之前先做 Whisper 可用性预检：
        #    若缺失 faster-whisper 或本地模型，则抛 WhisperNotReadyError，
        #    让 main() 输出结构化 JSON 提示，引导用户按需安装，而不是下到一半报错。
        ready, missing = _check_whisper_ready(model_size=model_size)
        if not ready:
            print("\n⚠️  字幕不可用，需回退 Whisper 转写，但 Whisper 环境未就绪：")
            for item in missing:
                print(f"     - {item}")
            raise WhisperNotReadyError(missing, suggested_size=model_size)

        # 延迟导入：确认可用后再 import，避免无 Whisper 依赖时启动即失败
        from transcriber import transcribe

        print("\n[2/3] 📥 下载音频（无字幕可用，回退 Whisper 方案）...")
        dl_meta = download_audio(
            url,
            download_dir,
            verbose=True,
            browser=effective_browser,
            cookies_file=effective_cookies,
            provider=provider,
        )
        print(f"    ✓ 音频: {dl_meta['audio_path']}")

        print("\n[3/3] 🎙️  Whisper 转写（GPU 加速）...")
        w_result = transcribe(
            dl_meta["audio_path"],
            model_size=model_size,
            language=language,
            verbose=True,
        )
        transcript_text = w_result["full_text"]
        timed_text = w_result["timed_text"]
        detected_lang = w_result["language"]
        elapsed = w_result["elapsed"]
        source_info = {
            "type": "whisper",
            "model": model_size,
            "audio_path": dl_meta["audio_path"],
            "platform": provider.name,
        }
        source_type = "whisper"

        # 清理音频
        if not keep_audio:
            try:
                Path(dl_meta["audio_path"]).unlink(missing_ok=True)
                print(f"🗑️  已清理音频文件")
            except Exception:
                pass

    # ===== 4) 写 transcript.md =====
    # 使用 downloader._safe_filename 保证与音频 / vtt 文件前缀完全一致
    safe_title = _safe_filename(meta["title"])
    md_name = f"{safe_title}_{meta['video_id']}.transcript.md"
    md_path = output_dir / md_name

    lines: list[str] = []
    lines.append(f"# 📺 {meta['title']}\n")

    if source_type == "subtitle":
        if source_info.get("subtitle_source") == "manual":
            src_label = f"{provider.display_name} 作者上传字幕"
        else:
            src_label = f"{provider.display_name} 自动字幕"
        lines.append(f"> **这是一份来自 {src_label} 的文字稿，可交给 AI 进行智能总结。**\n")
    else:
        lines.append(f"> **这是一份由 Whisper 自动转写的文字稿（源平台：{provider.display_name}），请将其交给 AI 进行智能总结。**\n")

    lines.append("## 📋 视频信息\n")
    lines.append(f"- **平台**: {provider.display_name}")
    lines.append(f"- **标题**: {meta['title']}")
    lines.append(f"- **作者**: {meta['uploader']}")
    lines.append(f"- **时长**: {_fmt_duration(meta['duration'])}")
    lines.append(f"- **URL**: {meta['url']}")
    lines.append(f"- **视频ID**: {meta['video_id']}")
    lines.append(f"- **语言**: {detected_lang}")
    if source_type == "subtitle":
        sub_src = source_info.get('subtitle_source', '')
        lines.append(f"- **来源**: {provider.display_name}字幕 ({sub_src})")
    else:
        lines.append(f"- **来源**: Whisper ({source_info.get('model','')})")
    if elapsed:
        lines.append(f"- **转写耗时**: {elapsed:.1f} 秒")
    lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if meta.get("description"):
        lines.append("## 📝 视频简介\n")
        lines.append(f"{meta['description']}\n")

    if source_type == "whisper":
        lines.append("## ⏱️ 带时间戳的转写（用于章节定位）\n")
        lines.append("```text")
        lines.append(timed_text)
        lines.append("```\n")

    lines.append("## 📖 完整文字稿（用于总结）\n")
    lines.append(transcript_text)
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 转写文档已生成: {md_path}")

    return {
        "md_path": str(md_path),
        "title": meta["title"],
        "url": meta["url"],
        "duration": meta["duration"],
        "language": detected_lang,
        "char_count": len(transcript_text),
        "source_type": source_type,
        "source_info": source_info,
        "platform": provider.name,
    }


def main():
    parser = argparse.ArgumentParser(
        description="视频下载 + 字幕/Whisper 本地转写（多平台：YouTube / B 站 / 抖音）",
    )
    parser.add_argument("url", nargs="?", help="视频 URL（YouTube / B 站 / 抖音等）")
    parser.add_argument("--file", "-f", help="从文件批量读取 URL（每行一个）")
    parser.add_argument("--platform", default=None,
                        choices=sorted(PROVIDERS_BY_NAME.keys()),
                        help="强制指定平台（不指定时从 URL 自动识别）")
    parser.add_argument("--list-platforms", action="store_true",
                        help="列出所有支持的平台并退出")
    parser.add_argument("--output-dir", "-o", default="./output", help="Markdown 输出目录")
    parser.add_argument("--download-dir", "-d", default="./downloads", help="音频临时目录")
    parser.add_argument("--model", "-m", default=None,
                        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                        help="Whisper 模型大小。不指定时自动选择本地最优模型（未找到则提示下载 medium）")
    parser.add_argument("--list-models", action="store_true",
                        help="列出本地已安装的 Whisper 模型并退出")
    parser.add_argument("--keep-audio", action="store_true", help="保留下载的音频文件")
    parser.add_argument("--language", "-l", default=None, help="强制指定语言 (如 zh/en)，默认自动检测")
    parser.add_argument("--browser", "-b", default=None,
                        help="从哪个浏览器读取 cookies: chrome/firefox/edge/brave 等。默认 chrome。传 'none' 禁用")
    parser.add_argument("--cookies-file", default=None,
                        help="指定 netscape 格式 cookies.txt 文件（优先级最高）")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "subtitle-only", "whisper-only"],
                        help="auto=先字幕后Whisper(默认) | subtitle-only=仅字幕 | whisper-only=仅Whisper")
    parser.add_argument("--sub-lang", default=None,
                        help="指定字幕语言，如 zh-Hans/zh-Hant/en，默认自动按中文>英文偏好挑选")
    parser.add_argument("--no-auto-sub", action="store_true",
                        help="禁止使用 YouTube 自动字幕（只接受作者上传的原生字幕）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出最终路径信息（给 skill 解析用）")

    args = parser.parse_args()

    # ===== 早期分支：--list-platforms =====
    if args.list_platforms:
        print("\n🌐 支持的视频平台：")
        for p in ALL_PROVIDERS:
            cookies_hint = p.cookies_filename or "(无)"
            print(f"  - {p.name:<10} {p.display_name:<8}  cookies: {cookies_hint}")
        sys.exit(0)

    # ===== 早期分支：--list-models =====
    if args.list_models:
        _print_local_models()
        sys.exit(0)

    # ===== 智能选择默认模型 =====
    if args.model is None:
        best = _pick_best_local_model()
        if best:
            args.model = best
            print(f"🎯 自动选择本地最优模型: {best}")
        else:
            # 本地一个模型都没有，先用“推荐值” medium，
            # 等真需要 Whisper 时再报错引导用户安装
            args.model = "medium"

    # 组装 URL 列表
    urls: list[str] = []
    if args.url:
        urls.append(args.url.strip())
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    if not urls:
        parser.print_help()
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve()
    download_dir = Path(args.download_dir).resolve()

    results: list[dict] = []
    errors: list[dict] = []

    for i, url in enumerate(urls, 1):
        if len(urls) > 1:
            print(f"\n\n########## 进度 {i}/{len(urls)} ##########")
        try:
            browser_arg = args.browser
            if browser_arg == "none":
                browser_arg = ""  # 显式禁用
            result = process_one(
                url=url,
                output_dir=output_dir,
                download_dir=download_dir,
                model_size=args.model,
                keep_audio=args.keep_audio,
                language=args.language,
                browser=browser_arg,
                cookies_file=args.cookies_file,
                mode=args.mode,
                sub_lang=args.sub_lang,
                allow_auto_sub=not args.no_auto_sub,
                platform=args.platform,
            )
            results.append(result)
        except WhisperNotReadyError as e:
            # 结构化错误：让 Skill / Agent 解析并引导用户安装
            print(f"\n⚠️ 需要 Whisper 但环境未就绪: {url}")
            suggested = e.suggested_size
            errors.append({
                "url": url,
                "error_type": "whisper_required",
                "error": str(e),
                "missing": e.missing,
                "install_guide": {
                    "step1_install_deps": "pip install -r requirements-whisper.txt",
                    "step2_download_model": f"python scripts/download_model.py --size {suggested}",
                    "suggested_model_size": suggested,
                    "model_size_options": {
                        "tiny":     "~75 MB  速度最快，准确度较差",
                        "base":     "~145 MB",
                        "small":    "~460 MB 中英文基本够用，体积小",
                        "medium":   "~1.4 GB ✅ 推荐（中英文较均衡）",
                        "large-v3": "~3.0 GB 准确度最高，占显存最多",
                    },
                    "cn_mirror_tip": "国内网络可加 --mirror https://hf-mirror.com 走 HF 镜像加速",
                    "retry_after_install": "安装完成后重跑：python main.py <URL> --json",
                },
            })
        except LoginRequiredError as e:
            print(f"\n⚠️ 需要登录 {e.platform}: {url}\n   原因：{e.reason}")
            errors.append(_build_login_required_entry(
                url=url,
                platform=e.platform,
                reason=e.reason,
                original_error=e.original_error or str(e),
            ))
        except Exception as e:
            err_msg = str(e)
            # 启发式升级：B 站 / 抖音等平台如果命中「需要登录」特征，
            # 转换为结构化 login_required 错误，便于 Agent 引导用户扫码登录
            inferred_platform = _infer_platform_from_url(url, explicit=args.platform)
            if (
                inferred_platform in _LOGIN_SCRIPTS
                and _looks_like_login_required(err_msg)
            ):
                print(f"\n⚠️ 疑似 {inferred_platform} cookies 失效或缺失，判定为需要登录: {url}")
                errors.append(_build_login_required_entry(
                    url=url,
                    platform=inferred_platform,
                    reason="下载失败且错误信息匹配登录失效特征（HTTP 403 / 未登录 / 账号状态 等）",
                    original_error=err_msg,
                ))
            else:
                print(f"\n❌ 处理失败: {url}\n   {err_msg}")
                traceback.print_exc()
                errors.append({"url": url, "error": err_msg})

    # 最终输出
    print(f"\n\n{'='*60}")
    print(f"🎉 全部完成！成功 {len(results)} / 失败 {len(errors)}")
    print(f"{'='*60}")
    for r in results:
        print(f"  ✓ {r['title']}")
        print(f"    → {r['md_path']}")

    if args.json:
        # 给 skill 用的结构化输出
        print("\n===JSON_RESULT_BEGIN===")
        print(json.dumps({"success": results, "errors": errors}, ensure_ascii=False))
        print("===JSON_RESULT_END===")

    sys.exit(0 if not errors else 2)


if __name__ == "__main__":
    main()
