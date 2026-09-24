"""
gui/core/model_manager.py —— Whisper 模型管理

模型目录固定为 <项目根>/models/<size>/，与 transcriber.py 的查找逻辑一致。
下载复用项目自带的 scripts/download_model.py（支持断点续传 + HF 镜像）。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import paths
from .env_manager import build_env
from .runner import ProcessTask

MIN_MODEL_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ModelSpec:
    size: str
    repo: str
    approx: str
    vram: str
    desc: str


# 按体积从小到大展示
MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("tiny", "Systran/faster-whisper-tiny", "~75 MB", "1 GB", "速度最快，准确度较差，适合快速试用"),
    ModelSpec("base", "Systran/faster-whisper-base", "~145 MB", "1 GB", "英文播客够用"),
    ModelSpec("small", "Systran/faster-whisper-small", "~460 MB", "2 GB", "中英文基本够用，体积小"),
    ModelSpec("medium", "Systran/faster-whisper-medium", "~1.4 GB", "5 GB", "⭐ 推荐，中英文较均衡"),
    ModelSpec("large-v2", "Systran/faster-whisper-large-v2", "~3.0 GB", "6 GB", "高准确度"),
    ModelSpec("large-v3", "Systran/faster-whisper-large-v3", "~3.0 GB", "6 GB", "准确度最高，中文最佳"),
)

SPEC_BY_SIZE = {s.size: s for s in MODEL_SPECS}

# 质量优先级（与 main.py 的 MODEL_QUALITY_RANK 保持一致）
QUALITY_RANK = ["large-v3", "large-v2", "medium", "small", "base", "tiny"]


@dataclass
class ModelStatus:
    spec: ModelSpec
    installed: bool
    bytes: int
    path: Path
    broken: bool = False        # 目录存在但 model.bin 缺失/过小

    @property
    def size(self) -> str:
        return self.spec.size

    @property
    def size_text(self) -> str:
        if self.installed:
            return paths.fmt_bytes(self.bytes)
        return self.spec.approx

    @property
    def status_text(self) -> str:
        if self.installed:
            return "✓ 已安装"
        if self.broken:
            return "! 不完整"
        return "未下载"


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def scan_models() -> list[ModelStatus]:
    """扫描全部候选模型的本地状态。"""
    root = paths.models_dir()
    out: list[ModelStatus] = []
    for spec in MODEL_SPECS:
        mdir = root / spec.size
        model_bin = mdir / "model.bin"
        installed = False
        broken = False
        total = 0
        if mdir.exists():
            total = _dir_size(mdir)
            try:
                if model_bin.is_file() and model_bin.stat().st_size >= MIN_MODEL_BYTES:
                    installed = True
                else:
                    broken = True
            except OSError:
                broken = True
        out.append(ModelStatus(spec=spec, installed=installed, bytes=total,
                               path=mdir, broken=broken))
    return out


def installed_models() -> list[ModelStatus]:
    """已安装模型，按质量从高到低排序。"""
    found = [m for m in scan_models() if m.installed]
    found.sort(key=lambda m: QUALITY_RANK.index(m.size) if m.size in QUALITY_RANK else 99)
    return found


def best_local_model() -> Optional[str]:
    found = installed_models()
    return found[0].size if found else None


def delete_model(size: str) -> tuple[bool, str]:
    mdir = paths.models_dir() / size
    if not mdir.exists():
        return False, f"目录不存在：{mdir}"
    try:
        shutil.rmtree(mdir)
        return True, f"已删除 {size}（{mdir}）"
    except Exception as exc:
        return False, f"删除失败：{exc}"


def download_task(python: str, size: str, cfg) -> ProcessTask:
    """构造模型下载任务（调用 scripts/download_model.py）。"""
    argv = [python, "scripts/download_model.py", "--size", size]
    mirror = str((cfg.get("hf_mirror") if cfg else "") or "").strip()
    if mirror:
        argv += ["--mirror", mirror]
    env = build_env({"HF_ENDPOINT": mirror} if mirror else None)
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=env,
                       title=f"下载 Whisper 模型 {size}")


def total_installed_bytes() -> int:
    return sum(m.bytes for m in scan_models() if m.installed or m.broken)
