"""
gui/core/media.py —— 本地媒体文件相关的界面无关逻辑

* 音视频扩展名判定
* 文件信息（大小、时长、类型）
* 目录扫描
* 「下载页 → 本地转写页」之间传递文件清单
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import paths
from .runner import CREATE_NO_WINDOW

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".opus", ".ogg", ".wma", ".mka"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".wmv",
              ".mpg", ".mpeg"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

FILE_DIALOG_TYPES = [
    ("音频/视频文件", " ".join(f"*{e}" for e in sorted(MEDIA_EXTS))),
    ("视频文件", " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))),
    ("音频文件", " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))),
    ("全部文件", "*.*"),
]


@dataclass
class MediaFile:
    path: Path
    bytes: int
    duration: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def kind(self) -> str:
        ext = self.path.suffix.lower()
        if ext in VIDEO_EXTS:
            return "视频"
        if ext in AUDIO_EXTS:
            return "音频"
        return "其他"

    @property
    def size_text(self) -> str:
        return paths.fmt_bytes(self.bytes)

    @property
    def duration_text(self) -> str:
        if self.duration <= 0:
            return "-"
        sec = int(self.duration)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def is_media(path: Path | str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS


def find_ffprobe() -> str | None:
    from . import env_manager

    ff = env_manager.find_ffmpeg()
    if ff:
        cand = Path(ff).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if cand.is_file():
            return str(cand)
    return shutil.which("ffprobe")


def probe_duration(path: Path | str, timeout: int = 20) -> float:
    """取媒体时长（秒）。没有 ffprobe 或失败时返回 0。"""
    exe = find_ffprobe()
    if not exe:
        return 0.0
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def make_media_file(path: Path | str, with_duration: bool = False) -> MediaFile | None:
    p = Path(path)
    try:
        if not p.is_file():
            return None
        size = p.stat().st_size
    except OSError:
        return None
    dur = probe_duration(p) if with_duration else 0.0
    return MediaFile(path=p, bytes=size, duration=dur)


def scan_dir(directory: Path | str, recursive: bool = False) -> list[Path]:
    """列出目录下的媒体文件。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    it = d.rglob("*") if recursive else d.glob("*")
    return sorted([p for p in it if p.is_file() and is_media(p)])


def expand_inputs(items: Iterable[Path | str], recursive: bool = False) -> list[Path]:
    """把「文件 + 目录」混合清单展开成媒体文件列表（去重、保序）。"""
    out: list[Path] = []
    seen: set[str] = set()
    for raw in items:
        p = Path(str(raw).strip().strip('"'))
        cands: Sequence[Path] = scan_dir(p, recursive) if p.is_dir() else [p]
        for c in cands:
            if not c.is_file() or not is_media(c):
                continue
            key = str(c.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out


def write_list_file(paths_: Sequence[Path | str], name: str = "gui_local_files.txt") -> Path:
    """把待转写文件清单写到临时文件（供 CLI --file 使用，规避超长命令行）。"""
    target = paths.logs_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(str(p) for p in paths_), encoding="utf-8")
    return target


def total_bytes(files: Iterable[MediaFile]) -> int:
    return sum(f.bytes for f in files)
