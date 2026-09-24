"""
transcribe_file.py —— 本地音视频文件转写（不下载）

与其他入口的关系：
    main.py             = 链接 → 字幕/下载 + 转写（一键流程）
    media_downloader.py = 链接 → 只下载文件
    transcribe_file.py  = 本地文件 → 只做 Whisper 转写（本文件）

支持的输入：
    * 音频：mp3 / m4a / wav / flac / aac / opus / ogg / wma
    * 视频：mp4 / mkv / webm / mov / avi / flv / ts …（自动用 ffmpeg 抽音轨）
    * 同目录字幕不参与，此入口一律走 Whisper

输出与 main.py 完全同构的 `*.transcript.md`，因此「输出管理」页能统一识别。

用法：
    python transcribe_file.py video.mp4
    python transcribe_file.py a.mp4 b.m4a --model medium
    python transcribe_file.py --file list.txt -o ./output
    python transcribe_file.py video.mp4 --language zh --json
    python transcribe_file.py --dir D:/videos            # 转写整个目录
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# 复用 main.py 里的模型检测逻辑，保证「装了没装、用哪个模型」判断口径一致
from main import (  # noqa: E402
    MODEL_QUALITY_RANK,
    WhisperNotReadyError,
    _check_whisper_ready,
    _fmt_duration,
    _pick_best_local_model,
)

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".opus", ".ogg", ".wma", ".mka"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".wmv", ".mpg", ".mpeg"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# faster-whisper 能直接读的格式（其余先用 ffmpeg 转 wav 更稳）
DIRECT_OK_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}


def find_ffmpeg() -> Optional[str]:
    """定位 ffmpeg：先看项目 bin/，再看 PATH（GUI 会把便携版目录注入 PATH）。"""
    local = Path(__file__).parent / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.is_file():
        return str(local)
    return shutil.which("ffmpeg")


def extract_audio(src: Path, workdir: Path, verbose: bool = True) -> tuple[Path, bool]:
    """把媒体文件转成 16kHz 单声道 wav。

    返回 (音频路径, 是否是新建的临时文件)。
    ffmpeg 不可用时原样返回源文件，交给 faster-whisper 自己解码。
    """
    ext = src.suffix.lower()
    ffmpeg = find_ffmpeg()

    if ext in DIRECT_OK_EXTS and ext != ".wav":
        # 这些格式 faster-whisper 能直接读，省一次转码
        return src, False

    if not ffmpeg:
        if ext in VIDEO_EXTS:
            print("    ⚠️  未找到 FFmpeg，将直接把视频交给 Whisper 解码（可能失败）")
            print("        建议到「环境安装」页点「安装 FFmpeg」")
        return src, False

    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / f"{src.stem}_16k.wav"
    if verbose:
        print(f"    🎧 正在抽取音轨（16kHz 单声道）...")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"    ⚠️  调用 FFmpeg 失败（{exc}），改为直接解码")
        return src, False

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        print(f"    ⚠️  音轨抽取失败，改为直接解码。FFmpeg 输出：\n        {err}")
        return src, False

    if verbose:
        mb = dst.stat().st_size / 1024 / 1024
        print(f"    ✓ 音轨就绪：{dst.name}（{mb:.1f} MB）")
    return dst, True


def collect_inputs(paths: list[str], directory: Optional[str] = None,
                   recursive: bool = False) -> list[Path]:
    """整理输入清单：展开目录、过滤非媒体文件、去重。"""
    out: list[Path] = []
    seen: set[Path] = set()

    def push(p: Path) -> None:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if rp in seen or not p.is_file():
            return
        if p.suffix.lower() not in MEDIA_EXTS:
            print(f"⚠️  跳过非音视频文件：{p.name}")
            return
        seen.add(rp)
        out.append(p)

    for raw in paths:
        p = Path(raw.strip().strip('"'))
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for f in sorted(it):
                push(f)
        else:
            push(p)

    if directory:
        d = Path(directory)
        if d.is_dir():
            it = d.rglob("*") if recursive else d.glob("*")
            for f in sorted(it):
                push(f)
        else:
            print(f"⚠️  目录不存在：{d}")
    return out


def _probe_duration(path: Path) -> float:
    """用 ffprobe 取时长（失败返回 0，仅用于展示）。"""
    ff = find_ffmpeg()
    if not ff:
        return 0.0
    probe = Path(ff).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    exe = str(probe) if probe.is_file() else shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════
def transcribe_one(
    src: Path,
    output_dir: Path,
    model_size: str = "medium",
    language: Optional[str] = None,
    keep_temp: bool = False,
    with_timestamps: bool = True,
    verbose: bool = True,
) -> dict:
    """转写一个本地文件并写出 transcript.md。"""
    from transcriber import transcribe   # 延迟导入：确认环境就绪后再加载

    output_dir.mkdir(parents=True, exist_ok=True)
    size_mb = src.stat().st_size / 1024 / 1024

    print(f"\n{'='*60}")
    print(f"📁 开始转写: {src.name}")
    print(f"    路径: {src}")
    print(f"    体积: {size_mb:.1f} MB")
    print(f"    模型: {model_size} | 语言: {language or '自动检测'}")
    print(f"{'='*60}")

    workdir = Path(tempfile.gettempdir()) / "video-summarizer-extract"
    audio_path, is_temp = extract_audio(src, workdir, verbose=verbose)

    try:
        print("\n🎙️  Whisper 转写中...")
        result = transcribe(
            str(audio_path),
            model_size=model_size,
            language=language,
            verbose=verbose,
        )
    finally:
        if is_temp and not keep_temp:
            try:
                Path(audio_path).unlink(missing_ok=True)
            except Exception:
                pass

    text = result["full_text"]
    timed = result["timed_text"]
    duration = result.get("duration") or _probe_duration(src)

    md_path = output_dir / f"{src.stem}.transcript.md"

    lines: list[str] = []
    lines.append(f"# 📺 {src.stem}\n")
    lines.append("> **这是一份由 Whisper 转写的本地文件文字稿，请将其交给 AI 进行智能总结。**\n")
    lines.append("## 📋 视频信息\n")
    lines.append("- **平台**: 本地文件")
    lines.append(f"- **标题**: {src.stem}")
    lines.append(f"- **作者**: -")
    lines.append(f"- **时长**: {_fmt_duration(duration)}")
    lines.append(f"- **URL**: {src}")
    lines.append(f"- **视频ID**: {src.stem[:40]}")
    lines.append(f"- **语言**: {result['language']}")
    lines.append(f"- **来源**: Whisper ({model_size})")
    lines.append(f"- **源文件**: {src.name}（{size_mb:.1f} MB）")
    lines.append(f"- **转写耗时**: {result['elapsed']:.1f} 秒")
    lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if with_timestamps:
        lines.append("## ⏱️ 带时间戳的转写（用于章节定位）\n")
        lines.append("```text")
        lines.append(timed)
        lines.append("```\n")

    lines.append("## 📖 完整文字稿（用于总结）\n")
    lines.append(text)
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 转写文档已生成: {md_path}")

    return {
        "md_path": str(md_path),
        "title": src.stem,
        "url": str(src),
        "source_file": str(src),
        "duration": duration,
        "language": result["language"],
        "char_count": len(text),
        "elapsed": result["elapsed"],
        "source_type": "whisper",
        "source_info": {"type": "whisper", "model": model_size, "platform": "local"},
        "platform": "local",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="对本地音频/视频文件做 Whisper 转写（不联网下载）",
    )
    parser.add_argument("files", nargs="*", help="音频/视频文件路径（可多个，也可传目录）")
    parser.add_argument("--file", "-f", help="从文本文件批量读取路径（每行一个）")
    parser.add_argument("--dir", "-D", default=None, help="转写整个目录下的媒体文件")
    parser.add_argument("--recursive", "-r", action="store_true", help="递归子目录")
    parser.add_argument("--output-dir", "-o", default="./output", help="Markdown 输出目录")
    parser.add_argument("--model", "-m", default=None,
                        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                        help="Whisper 模型（默认自动选本地最优）")
    parser.add_argument("--language", "-l", default=None, help="强制语言，如 zh / en（默认自动检测）")
    parser.add_argument("--no-timestamps", action="store_true", help="不输出带时间戳段落")
    parser.add_argument("--keep-temp", action="store_true", help="保留 ffmpeg 抽出的临时 wav")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 结果（供 GUI 解析）")

    args = parser.parse_args()

    raw_paths = list(args.files)
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        raw_paths.append(line)
        except OSError as exc:
            print(f"❌ 读取列表文件失败：{exc}")
            sys.exit(1)

    if not raw_paths and not args.dir:
        parser.print_help()
        sys.exit(1)

    inputs = collect_inputs(raw_paths, directory=args.dir, recursive=args.recursive)
    if not inputs:
        print("❌ 没有可转写的音视频文件")
        sys.exit(1)

    # ── 选模型 ──────────────────────────────────────────────────────────
    model_size = args.model
    if model_size is None:
        best = _pick_best_local_model()
        if best:
            model_size = best
            print(f"🎯 自动选择本地最优模型: {best}")
        else:
            model_size = "medium"

    # ── Whisper 预检（与 main.py 同一套判断）──────────────────────────────
    ready, missing = _check_whisper_ready(model_size=model_size)
    if not ready:
        print("\n⚠️  本地转写需要 Whisper，但环境未就绪：")
        for item in missing:
            print(f"     - {item}")
        payload = {
            "success": [],
            "errors": [{
                "url": str(inputs[0]),
                "error_type": "whisper_required",
                "error": "Whisper 环境未就绪，无法转写本地文件",
                "missing": missing,
                "install_guide": {
                    "step1_install_deps": "pip install -r requirements-whisper.txt",
                    "step2_download_model": f"python scripts/download_model.py --size {model_size}",
                    "suggested_model_size": model_size,
                    "model_size_options": {s: "" for s in MODEL_QUALITY_RANK},
                    "retry_after_install": "安装完成后重跑本地转写",
                },
            }],
        }
        if args.json:
            print("\n===JSON_RESULT_BEGIN===")
            print(json.dumps(payload, ensure_ascii=False))
            print("===JSON_RESULT_END===")
        sys.exit(2)

    output_dir = Path(args.output_dir).resolve()
    print(f"\n📋 待转写 {len(inputs)} 个文件，输出目录：{output_dir}")
    for p in inputs:
        print(f"    · {p.name}")

    results: list[dict] = []
    errors: list[dict] = []

    for i, src in enumerate(inputs, 1):
        if len(inputs) > 1:
            print(f"\n\n########## 进度 {i}/{len(inputs)} ##########")
        try:
            results.append(transcribe_one(
                src=src,
                output_dir=output_dir,
                model_size=model_size,
                language=args.language,
                keep_temp=args.keep_temp,
                with_timestamps=not args.no_timestamps,
            ))
        except WhisperNotReadyError as exc:
            errors.append({
                "url": str(src),
                "error_type": "whisper_required",
                "error": str(exc),
                "missing": exc.missing,
            })
        except Exception as exc:
            print(f"\n❌ 转写失败: {src}\n   {exc}")
            traceback.print_exc()
            errors.append({"url": str(src), "error": str(exc)})

    print(f"\n\n{'='*60}")
    print(f"🎉 全部完成！成功 {len(results)} / 失败 {len(errors)}")
    print(f"{'='*60}")
    for r in results:
        print(f"  ✓ {r['title']}  ({r['char_count']} 字)")
        print(f"    → {r['md_path']}")

    if args.json:
        print("\n===JSON_RESULT_BEGIN===")
        print(json.dumps({"success": results, "errors": errors}, ensure_ascii=False))
        print("===JSON_RESULT_END===")

    sys.exit(0 if not errors else 2)


if __name__ == "__main__":
    main()
