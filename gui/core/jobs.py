"""
gui/core/jobs.py —— 三种任务的命令行构造 + 结果解析

三条独立链路：
    JobKind.ONESHOT   → main.py             链接 → 字幕/下载 + 转写（一键）
    JobKind.DOWNLOAD  → media_downloader.py 链接 → 只下载文件
    JobKind.LOCAL     → transcribe_file.py  本地文件 → 只转写

把 argv 构造集中在这里，便于单独测试，也让三个页面的行为保持一致。
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Sequence

from . import env_manager, media, paths
from .runner import ProcessTask

JSON_BEGIN = "===JSON_RESULT_BEGIN==="
JSON_END = "===JSON_RESULT_END==="


class JobKind(str, Enum):
    ONESHOT = "oneshot"
    DOWNLOAD = "download"
    LOCAL = "local"


# ══════════════════════════════════════════════════════════════════════
# 结果解析
# ══════════════════════════════════════════════════════════════════════
def parse_result(lines: Sequence[str]) -> dict | None:
    """从子进程输出里取出 ===JSON_RESULT_BEGIN=== 包裹的结构化结果。"""
    try:
        text = "\n".join(lines)
        if JSON_BEGIN not in text:
            return None
        body = text.split(JSON_BEGIN, 1)[1].split(JSON_END, 1)[0].strip()
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
# ① 一键：下载/字幕 + 转写
# ══════════════════════════════════════════════════════════════════════
def build_oneshot_argv(
    python: str,
    urls: Sequence[str],
    output_dir: Path,
    *,
    platform: str = "auto",
    mode: str = "auto",
    model: str = "auto",
    language: str = "auto",
    browser: str = "chrome",
    sub_lang: str = "",
    keep_audio: bool = False,
    no_auto_sub: bool = False,
) -> list[str]:
    argv = [python, "main.py"]
    if len(urls) == 1:
        argv.append(urls[0])
    else:
        argv += ["--file", str(media.write_list_file(urls, "gui_batch_urls.txt"))]
    argv += ["--json", "-o", str(output_dir), "-d", str(paths.downloads_dir())]
    if platform != "auto":
        argv += ["--platform", platform]
    argv += ["--mode", mode]
    if model != "auto":
        argv += ["--model", model]
    if language != "auto":
        argv += ["--language", language]
    if sub_lang.strip():
        argv += ["--sub-lang", sub_lang.strip()]
    argv += ["--browser", browser]
    if keep_audio:
        argv.append("--keep-audio")
    if no_auto_sub:
        argv.append("--no-auto-sub")
    return argv


def oneshot_task(python: str, urls: Sequence[str], output_dir: Path, **kw) -> ProcessTask:
    argv = build_oneshot_argv(python, urls, output_dir, **kw)
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=env_manager.build_env(),
                       title=f"下载并转写 {len(urls)} 个视频", collect=True)


# ══════════════════════════════════════════════════════════════════════
# ② 只下载
# ══════════════════════════════════════════════════════════════════════
def build_download_argv(
    python: str,
    urls: Sequence[str],
    output_dir: Path,
    *,
    kind: str = "video",
    quality: str = "best",
    container: str = "mp4",
    audio_format: str = "keep",
    subs: bool = False,
    sub_langs: str = "",
    thumbnail: bool = False,
    embed_metadata: bool = False,
    platform: str = "auto",
    browser: str = "chrome",
) -> list[str]:
    argv = [python, "media_downloader.py"]
    if len(urls) == 1:
        argv.append(urls[0])
    else:
        argv += ["--file", str(media.write_list_file(urls, "gui_download_urls.txt"))]
    argv += ["--json", "-o", str(output_dir), "--kind", kind]
    if kind == "video":
        argv += ["--quality", quality, "--container", container]
    else:
        argv += ["--audio-format", audio_format]
    if subs:
        argv.append("--subs")
        if sub_langs.strip():
            argv += ["--sub-langs", sub_langs.strip()]
    if thumbnail:
        argv.append("--thumbnail")
    if embed_metadata:
        argv.append("--embed-metadata")
    if platform != "auto":
        argv += ["--platform", platform]
    argv += ["--browser", browser]
    return argv


def download_task(python: str, urls: Sequence[str], output_dir: Path, **kw) -> ProcessTask:
    argv = build_download_argv(python, urls, output_dir, **kw)
    kind_text = "音频" if kw.get("kind") == "audio" else "视频"
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=env_manager.build_env(),
                       title=f"下载 {len(urls)} 个{kind_text}", collect=True)


# ══════════════════════════════════════════════════════════════════════
# ③ 本地文件转写
# ══════════════════════════════════════════════════════════════════════
def build_local_argv(
    python: str,
    files: Sequence[Path | str],
    output_dir: Path,
    *,
    model: str = "auto",
    language: str = "auto",
    timestamps: bool = True,
    keep_temp: bool = False,
) -> list[str]:
    argv = [python, "transcribe_file.py"]
    if len(files) == 1:
        argv.append(str(files[0]))
    else:
        argv += ["--file", str(media.write_list_file(files, "gui_local_files.txt"))]
    argv += ["--json", "-o", str(output_dir)]
    if model != "auto":
        argv += ["--model", model]
    if language != "auto":
        argv += ["--language", language]
    if not timestamps:
        argv.append("--no-timestamps")
    if keep_temp:
        argv.append("--keep-temp")
    return argv


def local_task(python: str, files: Sequence[Path | str], output_dir: Path, **kw) -> ProcessTask:
    argv = build_local_argv(python, files, output_dir, **kw)
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=env_manager.build_env(),
                       title=f"转写 {len(files)} 个本地文件", collect=True)
