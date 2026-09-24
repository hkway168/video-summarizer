"""
subtitle_fetcher.py —— 视频字幕抓取模块（多平台：YouTube / B 站 / 抖音 / ...）
优先级：作者上传的原生字幕 > 自动生成字幕 > 返回 None (让调用方回退 Whisper)

使用 yt-dlp CLI 下载 vtt 字幕，然后清洗为纯文本（去掉时间戳、去重、合并段落）。
通过 providers.BaseProvider 注入平台差异（cookies 文件名、deno、额外 yt-dlp flag 等）。
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from cookies_utils import ensure_netscape
from providers import BaseProvider, resolve_provider
from providers.youtube import YouTubeProvider


# 默认（向后兼容 YouTube）的语言优先级
PREFERRED_LANGS = [
    "zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK",  # 中文
    "en", "en-US", "en-GB",  # 英文
]

# 伪字幕轨道：yt-dlp 的 subtitles 字段里会夹带这些非字幕轨道，
# 选中后无法得到 vtt，必须在选轨和下载时直接过滤掉。
#   - live_chat / rechat：直播聊天记录（会以 json 格式存在，不是字幕）
IGNORED_SUB_LANGS = {"live_chat", "rechat"}


def filter_real_subs(raw: Optional[dict]) -> dict:
    """过滤掉 yt-dlp 返回的 subtitles 里的伪字幕轨道。"""
    if not raw:
        return {}
    return {
        k: v for k, v in raw.items()
        if k not in IGNORED_SUB_LANGS and isinstance(v, list) and v
    }


def _find_deno() -> Optional[str]:
    script_dir = Path(__file__).parent
    for c in [script_dir / "bin" / "deno.exe", script_dir / "bin" / "deno"]:
        if c.exists():
            return str(c)
    import shutil
    return shutil.which("deno")


def _ensure_deno_on_path() -> Optional[str]:
    deno_path = _find_deno()
    if not deno_path:
        return None
    bin_dir = str(Path(deno_path).parent)
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + current
    return deno_path


def _build_base_args(
    cookies_file: Optional[str],
    browser: Optional[str],
    provider: Optional[BaseProvider] = None,
) -> list[str]:
    if provider is None:
        provider = YouTubeProvider()

    args = ["--no-playlist", "--retries", "5", "--socket-timeout", "30",
            "--quiet", "--no-warnings"]

    # deno 仅在需要的平台（YouTube）启用
    if provider.needs_deno:
        deno = _ensure_deno_on_path()
        if deno:
            args += ["--js-runtimes", "deno", "--remote-components", "ejs:github"]

    # 平台自定义 flag
    if provider.extra_ytdlp_args:
        args += list(provider.extra_ytdlp_args)

    # cookies
    if cookies_file:
        args += ["--cookies", str(cookies_file)]
    elif browser:
        args += ["--cookies-from-browser", browser]
    return args


def _run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "yt_dlp", *args]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _probe_available_subs(
    url: str,
    cookies_file: Optional[str],
    browser: Optional[str],
    provider: Optional[BaseProvider] = None,
) -> tuple[dict, dict]:
    """
    用 --dump-json 获取视频 info，返回 (subtitles, automatic_captions)。
    subtitles 是作者上传的，automatic_captions 是 YouTube 自动生成的。
    伪字幕轨道（如 live_chat）会被过滤掉。
    """
    base = _build_base_args(cookies_file, browser, provider=provider)
    # 注意：--write-auto-subs 不和 --dump-json 冲突，但我们只想探测，不需要 write
    result = _run_ytdlp([*base, "--dump-json", "--skip-download", url])
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"获取视频字幕信息失败:\n{result.stderr[:600]}")
    info = json.loads(result.stdout.strip().splitlines()[0])
    return (
        filter_real_subs(info.get("subtitles")),
        filter_real_subs(info.get("automatic_captions")),
    )


def _pick_best_lang(available: dict) -> Optional[str]:
    """按 PREFERRED_LANGS 顺序选出最合适的语言代码；都没有则取第一个可用。
    自动过滤 live_chat 等伪字幕轨道。
    """
    available = filter_real_subs(available)
    if not available:
        return None
    # 精确匹配
    for lang in PREFERRED_LANGS:
        if lang in available:
            return lang
    # 前缀匹配（比如 available 里是 "zh-Hant-en" 这种奇怪的）
    for lang in PREFERRED_LANGS:
        for k in available.keys():
            if k.startswith(lang + "-") or k == lang:
                return k
    # 兜底：第一个（已是过滤后的，不会再选中 live_chat）
    return next(iter(available.keys()))


def _clean_vtt_to_text(vtt_content: str) -> str:
    """
    清洗 VTT 字幕 → 纯文本。
    - 去掉 WEBVTT 头
    - 去掉时间戳行 (00:00:01.000 --> 00:00:03.000)
    - 去掉 <c>、<00:00:01.234><c> 等 HTML/内联标签
    - 去重（YouTube 自动字幕常有滚动重复行）
    - 按句号/问号/感叹号合并成段落
    """
    lines = vtt_content.splitlines()
    text_lines: list[str] = []
    prev_line = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 跳过头部和元信息
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        # 跳过时间戳行  "00:00:01.000 --> 00:00:03.000 ..."
        if "-->" in line:
            continue
        # 跳过纯数字的 cue id
        if line.isdigit():
            continue
        # 去掉 VTT 内联时间戳 <00:00:01.234>
        line = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", line)
        # 去掉 <c> / </c> / <c.colorXXX> 标签
        line = re.sub(r"</?c[^>]*>", "", line)
        # 去掉其他 HTML 实体 &nbsp; &amp;
        line = line.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        line = line.strip()
        if not line:
            continue
        # 去重：自动字幕会"滚动"——下一段包含上一段内容
        if line == prev_line:
            continue
        # 如果当前行是上一行的延续（后半部分重复），只保留增量
        if prev_line and line.startswith(prev_line):
            incr = line[len(prev_line):].strip()
            if incr:
                text_lines.append(incr)
            prev_line = line
            continue
        text_lines.append(line)
        prev_line = line

    raw = " ".join(text_lines)
    # 去掉多余空白
    raw = re.sub(r"\s+", " ", raw).strip()
    # 中英混排优化：中文后的空格去掉，英文间保留
    raw = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", raw)
    # 按句号等标点分段
    raw = re.sub(r"([。！？!?]) ?", r"\1\n\n", raw)
    return raw.strip()


def fetch_subtitle(
    url: str,
    out_dir: Path,
    video_id: str,
    safe_title: str,
    cookies_file: Optional[str] = None,
    browser: Optional[str] = None,
    prefer_lang: Optional[str] = None,
    allow_auto: bool = True,
    verbose: bool = True,
    provider: Optional[BaseProvider] = None,
) -> Optional[dict]:
    """
    尝试下载视频字幕。
    
    参数：
        url:          视频链接
        out_dir:      输出目录
        video_id:     视频ID（用于文件名）
        safe_title:   清洗后的标题
        cookies_file: netscape 格式 cookies（会自动转换 JSON）
        browser:      浏览器 cookies
        prefer_lang:  指定语言（如 "zh-Hant" / "en"），None 则自动挑选
        allow_auto:   是否允许回退到自动字幕
        verbose:      是否打日志
        provider:     平台实例；None 时根据 url 自动识别

    返回：
        成功: {"text": 清洗后纯文本, "lang": 语言码, "source": "manual"|"auto", "vtt_path": 原始vtt路径}
        失败: None
    """
    if provider is None:
        provider = resolve_provider(url)

    # cookies 格式自动转换
    if cookies_file:
        try:
            cookies_file = str(ensure_netscape(cookies_file))
        except Exception as e:
            if verbose:
                print(f"    [字幕] cookies 转换失败: {e}")
            cookies_file = None

    # prefer_lang 也要过滤掉伪字幕（如上游 main.py 预判结果为 live_chat）
    if prefer_lang and prefer_lang in IGNORED_SUB_LANGS:
        if verbose:
            print(f"    [字幕] 忽略伪字幕预判: {prefer_lang}")
        prefer_lang = None

    # 1) 探测可用字幕
    try:
        subs, auto_subs = _probe_available_subs(url, cookies_file, browser, provider=provider)
    except RuntimeError as e:
        if verbose:
            print(f"    [字幕] 探测失败: {e}")
        return None
    # 2) 决定抓哪个
    lang = None
    source = None
    write_flag = None

    if prefer_lang and prefer_lang in subs:
        lang, source, write_flag = prefer_lang, "manual", "--write-subs"
    elif prefer_lang and allow_auto and prefer_lang in auto_subs:
        lang, source, write_flag = prefer_lang, "auto", "--write-auto-subs"
    else:
        # 先找原生
        cand = _pick_best_lang(subs)
        if cand:
            lang, source, write_flag = cand, "manual", "--write-subs"
        elif allow_auto:
            cand = _pick_best_lang(auto_subs)
            if cand:
                lang, source, write_flag = cand, "auto", "--write-auto-subs"

    if not lang:
        if verbose:
            print("    [字幕] 没有找到可用字幕")
        return None

    if verbose:
        print(f"    [字幕] 已选: {lang} ({source})")

    # 3) 下载 VTT
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vtt_template = str(out_dir / f"{safe_title}_{video_id}.%(ext)s")

    base = _build_base_args(cookies_file, browser, provider=provider)
    dl_args = [
        *base,
        "--skip-download",
        write_flag,
        "--sub-langs", lang,
        "--sub-format", "vtt",
        "-o", vtt_template,
        url,
    ]
    result = _run_ytdlp(dl_args)
    if result.returncode != 0:
        if verbose:
            print(f"    [字幕] 下载失败: {result.stderr[:300]}")
        return None

    # 4) 找到 vtt 文件
    vtt_files = list(out_dir.glob(f"{safe_title}_{video_id}.{lang}.vtt"))
    if not vtt_files:
        # 兜底扫描
        vtt_files = list(out_dir.glob(f"{safe_title}_{video_id}*.vtt"))
    if not vtt_files:
        if verbose:
            print(f"    [字幕] 下载完成但找不到 vtt 文件")
        return None
    vtt_path = vtt_files[0]

    # 5) 清洗 → 纯文本
    try:
        vtt_content = vtt_path.read_text(encoding="utf-8")
    except Exception as e:
        if verbose:
            print(f"    [字幕] 读取 vtt 失败: {e}")
        return None

    text = _clean_vtt_to_text(vtt_content)
    if not text or len(text) < 50:
        if verbose:
            print(f"    [字幕] 清洗后文本太短 ({len(text)}字)，视为失败")
        return None

    if verbose:
        print(f"    [字幕] ✓ 提取成功 | 语言={lang} | 来源={source} | 字数={len(text)}")

    return {
        "text": text,
        "lang": lang,
        "source": source,
        "vtt_path": str(vtt_path),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python subtitle_fetcher.py <YouTube URL> [--cookies <file>] [--lang <code>]")
        sys.exit(1)

    url = sys.argv[1]
    cookies = None
    lang = None
    for i, a in enumerate(sys.argv):
        if a == "--cookies" and i + 1 < len(sys.argv):
            cookies = sys.argv[i + 1]
        if a == "--lang" and i + 1 < len(sys.argv):
            lang = sys.argv[i + 1]

    # 单独测试时：尝试探测视频真实 id + 标题，以便命名和正式流程保持一致
    # 探测失败就退回到 _debug 占位，但输出到独立目录，避免污染 ./output
    try:
        from downloader import probe_metadata_only, _safe_filename  # 延迟导入，避免循环依赖
        meta = probe_metadata_only(url, cookies_file=cookies)
        _vid = meta["video_id"]
        _title = _safe_filename(meta["title"])
    except Exception as _e:
        print(f"[warn] 元数据探测失败: {_e}，使用占位命名")
        _vid = "unknown"
        _title = "_debug_subtitle"

    test_dir = Path("./output/_subtitle_test")
    result = fetch_subtitle(
        url=url,
        out_dir=test_dir,
        video_id=_vid,
        safe_title=_title,
        cookies_file=cookies,
        prefer_lang=lang,
    )
    if result:
        print(f"\n✓ 抓取成功 | 语言={result['lang']} | 来源={result['source']}")
        print(f"前 500 字:\n{result['text'][:500]}")
    else:
        print("✗ 抓取失败")
