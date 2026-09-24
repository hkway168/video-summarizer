"""
gui/core/outputs.py —— 输出内容（transcript.md）管理

负责扫描输出目录、解析文档头部元信息、预览、另存、删除、清理音频缓存。
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from . import paths

TITLE_RE = re.compile(r"^#\s*(?:📺\s*)?(.+?)\s*$", re.M)
FIELD_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s*[:：]\s*(.*)$", re.M)
HEAD_BYTES = 8192


@dataclass
class OutputDoc:
    path: Path
    title: str
    platform: str
    source: str
    duration: str
    author: str
    url: str
    created: str
    bytes: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_text(self) -> str:
        return paths.fmt_bytes(self.bytes)

    @property
    def mtime_text(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")

    def search_blob(self) -> str:
        return " ".join([self.name, self.title, self.platform, self.author, self.url]).lower()


def _parse_head(path: Path) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(HEAD_BYTES)
    except Exception:
        return {}
    data: dict[str, str] = {}
    m = TITLE_RE.search(head)
    if m:
        data["标题"] = m.group(1).strip()
    for key, val in FIELD_RE.findall(head):
        data.setdefault(key.strip(), val.strip())
    return data


def scan_outputs(output_dir: Path | str, extra_dirs: Iterable[Path] = ()) -> list[OutputDoc]:
    """扫描输出目录下的 markdown 文档，按修改时间倒序。"""
    dirs = [Path(output_dir)] + [Path(d) for d in extra_dirs]
    seen: set[Path] = set()
    docs: list[OutputDoc] = []
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            try:
                st = p.stat()
            except OSError:
                continue
            meta = _parse_head(p)
            docs.append(OutputDoc(
                path=p,
                title=meta.get("标题") or p.stem,
                platform=meta.get("平台", ""),
                source=meta.get("来源", ""),
                duration=meta.get("时长", ""),
                author=meta.get("作者", ""),
                url=meta.get("URL", ""),
                created=meta.get("生成时间", ""),
                bytes=st.st_size,
                mtime=st.st_mtime,
            ))
    docs.sort(key=lambda d: d.mtime, reverse=True)
    return docs


def read_text(path: Path | str, limit: int = 400_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(limit + 1)
        if len(text) > limit:
            return text[:limit] + "\n\n…（内容过长，仅预览前 400,000 字，请点「打开文件」查看全文）"
        return text
    except Exception as exc:
        return f"（读取失败：{exc}）"


def delete_doc(path: Path | str) -> tuple[bool, str]:
    p = Path(path)
    try:
        p.unlink()
        return True, f"已删除 {p.name}"
    except Exception as exc:
        return False, f"删除失败：{exc}"


def save_as(src: Path | str, dst: Path | str) -> tuple[bool, str]:
    try:
        shutil.copy2(src, dst)
        return True, f"已另存为 {dst}"
    except Exception as exc:
        return False, f"另存失败：{exc}"


def clean_downloads(download_dir: Path | str | None = None) -> tuple[int, int]:
    """清空音频/字幕临时目录，返回 (删除文件数, 释放字节数)。"""
    d = Path(download_dir) if download_dir else paths.downloads_dir()
    if not d.exists():
        return 0, 0
    n = 0
    freed = 0
    for p in d.rglob("*"):
        if p.is_file():
            try:
                sz = p.stat().st_size
                p.unlink()
                n += 1
                freed += sz
            except Exception:
                pass
    for p in sorted([x for x in d.rglob("*") if x.is_dir()], reverse=True):
        try:
            p.rmdir()
        except Exception:
            pass
    return n, freed


def dir_size(path: Path | str) -> int:
    total = 0
    p = Path(path)
    if not p.exists():
        return 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def summarize(docs: list[OutputDoc]) -> str:
    if not docs:
        return "暂无输出文档"
    total = sum(d.bytes for d in docs)
    return f"共 {len(docs)} 个文档 · {paths.fmt_bytes(total)}"


def find_doc(docs: list[OutputDoc], path: Path | str) -> Optional[OutputDoc]:
    target = Path(path).resolve()
    for d in docs:
        try:
            if d.path.resolve() == target:
                return d
        except Exception:
            continue
    return None
