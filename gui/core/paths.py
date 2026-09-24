"""
gui/core/paths.py —— 运行目录解析（绿色软件 / 免安装模式）

原则：**所有生成的内容都放在 exe 旁边，不写系统目录**，方便整体管理与搬移。

两种运行形态：

1. 项目内（python app_gui.py，或 exe 就放在 video-summarizer 目录里）
       RUNTIME_DIR = DATA_DIR = 项目根目录（含 main.py / providers/ ...）

2. 独立（exe 被单独拷到桌面等位置，同级没有 main.py）
       RUNTIME_DIR = DATA_DIR = <exe 所在目录>/VideoSummarizer-Data
       内置脚本 + 模型 + 输出 + 日志全部集中在这一个文件夹里，
       删除它即等于「卸载」，拷走它即等于「搬家」。

   ⚠️ 只创建这一个文件夹，绝不把零散 .py 撒在 exe 旁边
      （v1.0.0 有此问题，由 stray_payload_files() / cleanup_stray_payload() 清理）

所有子进程（main.py / scripts/download_model.py）都以 RUNTIME_DIR 为 cwd 运行，
这样 main.py、transcriber.py 里基于 `Path(__file__).parent` 的
models/ 、cookies 、bin/deno.exe 定位逻辑完全无需改动。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Optional

# 判断一个目录是否是「项目根目录」的标志文件
PROJECT_MARKERS = ("main.py", "downloader.py", "subtitle_fetcher.py",
                   "media_downloader.py", "transcribe_file.py")

# PyInstaller 打包时塞进去的源码子目录名（见 build_exe.py）
PAYLOAD_DIRNAME = "payload"

# 需要随 exe 一起分发的源码清单（体积都很小）
PAYLOAD_FILES = (
    "main.py",
    "media_downloader.py",
    "transcribe_file.py",
    "downloader.py",
    "subtitle_fetcher.py",
    "transcriber.py",
    "cookies_utils.py",
    "requirements.txt",
    "requirements-whisper.txt",
    "requirements-login.txt",
    "requirements-douyin.txt",
)
# scripts/ 里含 _browser_finder.py（GUI 探测本机浏览器时会加载它）
PAYLOAD_DIRS = ("providers", "scripts")

#: 独立态时，在 exe 旁边创建的唯一工作文件夹名
WORK_DIRNAME = "VideoSummarizer-Data"

#: 释放源码后写下的标记文件，用来区分「程序自动释放的目录」与「用户的真实项目目录」
EXTRACT_STAMP = ".video-summarizer-app"

#: 真实项目独有的文件（payload 里没有）。据此判断某目录是不是用户的源码仓库
PROJECT_ONLY_MARKERS = ("app_gui.py", "build_exe.py", "SKILL.md", "gui")

#: 程序自动创建的数据子目录（清理残留时一并考虑）
DATA_SUBDIRS = ("output", "downloads", "videos", "logs", "tools", "models")

_runtime_dir: Optional[Path] = None
_data_dir: Optional[Path] = None


def is_frozen() -> bool:
    """当前是否运行在 PyInstaller 打包出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Optional[Path]:
    """PyInstaller 解包临时目录（_MEIPASS），非打包态返回 None。"""
    mei = getattr(sys, "_MEIPASS", None)
    return Path(mei) if mei else None


def exe_dir() -> Path:
    """exe（或开发态解释器脚本）所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _looks_like_project(path: Path) -> bool:
    try:
        return all((path / name).exists() for name in PROJECT_MARKERS)
    except OSError:
        return False


def _is_real_project(path: Path) -> bool:
    """是不是「用户的源码项目目录」（而非程序自动释放出来的一堆脚本）。

    真实项目除了 main.py 之外，还会有 app_gui.py / build_exe.py / SKILL.md / gui/，
    这些都不在 exe 的 payload 里。
    """
    if not _looks_like_project(path):
        return False
    try:
        return any((path / name).exists() for name in PROJECT_ONLY_MARKERS)
    except OSError:
        return False


def _is_extracted_app(path: Path) -> bool:
    """是不是本程序释放出来的运行目录（带标记文件）。"""
    try:
        return (path / EXTRACT_STAMP).exists() and _looks_like_project(path)
    except OSError:
        return False


def work_dir() -> Path:
    """独立态的工作文件夹：<exe 所在目录>/VideoSummarizer-Data。

    这是绿色软件的核心 —— 程序的一切（脚本、模型、输出、日志、工具）
    都在这一个文件夹里，跟 exe 放在一起，便于整体拷走或删除。
    """
    return exe_dir() / WORK_DIRNAME


def legacy_app_home() -> Path:
    """旧版本（1.0.1）曾使用的系统目录，仅用于检测与迁移提示。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "VideoSummarizer"


def _iter_candidates() -> Iterator[Path]:
    """按优先级给出可能的「项目目录」。"""
    if is_frozen():
        base = exe_dir()          # 统一走 exe_dir()，便于测试时替换
        # 之前释放过的工作文件夹（优先，保证重复启动稳定落到同一处）
        yield base / WORK_DIRNAME
        # exe 就在项目里（便携用法）：自身 → 上一级 → 上两级
        yield base
        yield base.parent
        yield base.parent.parent
    else:
        here = Path(__file__).resolve()
        yield here.parents[2]
        yield Path.cwd()


def _extract_payload(target: Path) -> bool:
    """把打包进 exe 的源码释放到 target 目录。成功返回 True。"""
    bundle = bundle_dir()
    if not bundle:
        return False
    payload = bundle / PAYLOAD_DIRNAME
    if not payload.exists():
        return False
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in PAYLOAD_FILES:
            src = payload / name
            if src.exists() and not (target / name).exists():
                shutil.copy2(src, target / name)
        for name in PAYLOAD_DIRS:
            src = payload / name
            if src.exists():
                shutil.copytree(src, target / name, dirs_exist_ok=True)
        # 打上标记，便于以后识别 / 清理，也避免误判成用户项目
        (target / EXTRACT_STAMP).write_text(
            "此目录由 视频转写助手 自动生成，可随程序一起删除。\n",
            encoding="utf-8",
        )
    except OSError:
        return False
    return _looks_like_project(target)


def runtime_dir() -> Path:
    """项目脚本所在目录（子进程 cwd）。"""
    global _runtime_dir
    if _runtime_dir is not None:
        return _runtime_dir

    # 1) 已有的工作文件夹 / 真实项目目录
    for cand in _iter_candidates():
        if _is_extracted_app(cand) or _is_real_project(cand):
            _runtime_dir = cand
            return _runtime_dir

    # 2) 独立态：在 exe 旁边建一个 VideoSummarizer-Data 文件夹并释放脚本
    target = work_dir()
    if _looks_like_project(target) or _extract_payload(target):
        _runtime_dir = target
        return _runtime_dir

    # 3) 兜底：仍指向工作文件夹（界面会通过 project_ready() 提示异常）；
    #    开发态退回源码目录。
    _runtime_dir = target if is_frozen() else exe_dir()
    return _runtime_dir


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(path), prefix=".wtest", delete=True):
            return True
    except Exception:
        return False


def is_standalone() -> bool:
    """是否处于「独立态」（exe 不在项目目录里，已自建工作文件夹）。"""
    try:
        return runtime_dir().resolve() == work_dir().resolve()
    except OSError:
        return runtime_dir() == work_dir()


def data_dir() -> Path:
    """可写数据根目录。绿色模式下 = runtime_dir()，即所有东西都在一起。"""
    global _data_dir
    if _data_dir is not None:
        return _data_dir
    rt = runtime_dir()
    if _is_writable(rt):
        _data_dir = rt
    else:
        # 极端情况：exe 放在只读位置（如光盘 / Program Files 且无权限）
        # 才退回系统目录，保证程序仍能用
        _data_dir = legacy_app_home()
    try:
        _data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return _data_dir


def data_dir_is_fallback() -> bool:
    """数据目录是否因为 exe 所在位置不可写而退回了系统目录。"""
    try:
        return data_dir().resolve() != runtime_dir().resolve()
    except OSError:
        return False


# ── 具体子目录 ──────────────────────────────────────────────────────────
def models_dir() -> Path:
    """Whisper 模型目录。必须跟 main.py 同级，否则 transcriber 找不到。"""
    return runtime_dir() / "models"


def default_output_dir() -> Path:
    return data_dir() / "output"


def downloads_dir() -> Path:
    """转写流程用的临时音频目录（会被「清理缓存」清空）。"""
    return data_dir() / "downloads"


def videos_dir() -> Path:
    """「只下载」保存视频/音频的目录（正式产物，不会被清理缓存删除）。"""
    return data_dir() / "videos"


def logs_dir() -> Path:
    return data_dir() / "logs"


def tools_dir() -> Path:
    """GUI 自行下载的第三方工具（ffmpeg / 内置 python 等）。"""
    return data_dir() / "tools"


def venv_dir() -> Path:
    return data_dir() / ".venv"


def deno_dir() -> Path:
    return runtime_dir() / "bin"


def config_path() -> Path:
    return data_dir() / "gui_config.json"


def ensure_dirs() -> None:
    for p in (default_output_dir(), downloads_dir(), videos_dir(),
              logs_dir(), tools_dir(), models_dir()):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def project_ready() -> bool:
    """项目脚本是否就绪。"""
    return _looks_like_project(runtime_dir())


# ══════════════════════════════════════════════════════════════════════
# 残留文件清理
#
# v1.0.0 的 exe 在独立运行时会把内置脚本**零散地**释放到 exe 所在目录，
# 于是把 exe 放桌面就会在桌面生成一堆 .py / 文件夹。
# 现在统一收纳进 VideoSummarizer-Data 文件夹，这里负责识别并清掉旧版残留。
# ══════════════════════════════════════════════════════════════════════
def _stray_scan_dir() -> Path:
    return exe_dir()


def has_stray_payload(target: Path | None = None) -> bool:
    """exe 旁边是否存在旧版散落的残留文件。"""
    return bool(stray_payload_files(target))


def stray_payload_files(target: Path | None = None) -> list[Path]:
    """列出 exe 旁边由旧版散落生成、可安全删除的文件/目录。

    安全策略（任一不满足就返回空，绝不误删用户文件）：
      * 只在打包态生效
      * 该目录不能是「用户的真实项目目录」（有 app_gui.py / build_exe.py / SKILL.md / gui）
      * 不碰当前正在使用的工作文件夹 VideoSummarizer-Data
      * 只匹配 payload 里已知的文件名与目录名，外加程序生成的数据目录/配置
      * 必须确实存在散落的脚本（main.py 等），否则视为「无残留」
    """
    base = Path(target) if target else _stray_scan_dir()
    if not is_frozen():
        return []
    try:
        if not base.is_dir():
            return []
    except OSError:
        return []
    # 用户的真实项目目录 → 一律不动
    if _is_real_project(base):
        return []
    # 当前工作文件夹本身、或位于它内部 → 一律不动
    # 注意：exe 目录是工作文件夹的父目录，不能因此被排除（否则永远扫不到残留）
    try:
        wd = work_dir().resolve()
        b = base.resolve()
        if b == wd or wd in b.parents:
            return []
    except OSError:
        pass

    found: list[Path] = []
    for name in PAYLOAD_FILES:
        p = base / name
        if p.is_file():
            found.append(p)
    # 只有确实散落了脚本才继续，避免误伤同名目录
    if not found:
        return []

    for name in (*PAYLOAD_DIRS, *DATA_SUBDIRS, "__pycache__", ".venv", "bin"):
        if name == WORK_DIRNAME:     # 双保险：绝不把工作文件夹列进去
            continue
        p = base / name
        if p.is_dir():
            found.append(p)
    for name in ("gui_config.json", EXTRACT_STAMP):
        p = base / name
        if p.is_file():
            found.append(p)
    return found


# ── 旧版系统目录（v1.0.1 曾把数据放这里）───────────────────────────────
def legacy_appdata_summary() -> tuple[int, int]:
    """旧版系统数据目录的 (条目数, 字节数)；不存在返回 (0, 0)。"""
    d = legacy_app_home()
    try:
        if not d.is_dir():
            return 0, 0
        # 若当前正用它当数据目录（exe 位置不可写的兜底），不提示清理
        if data_dir().resolve() == d.resolve():
            return 0, 0
        items = list(d.iterdir())
        total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return len(items), total
    except OSError:
        return 0, 0


def cleanup_legacy_appdata() -> tuple[bool, str]:
    """删除旧版系统数据目录。返回 (是否成功, 说明)。"""
    d = legacy_app_home()
    try:
        if not d.is_dir():
            return True, "旧目录不存在，无需清理"
        if data_dir().resolve() == d.resolve():
            return False, "当前正在使用该目录，不能删除"
        shutil.rmtree(d)
        return True, f"已删除旧数据目录：{d}"
    except Exception as exc:
        return False, f"删除失败：{exc}"


def stray_payload_summary(target: Path | None = None) -> tuple[int, int]:
    """返回 (条目数, 占用字节数)。"""
    items = stray_payload_files(target)
    total = 0
    for p in items:
        try:
            if p.is_file():
                total += p.stat().st_size
            else:
                total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        except OSError:
            pass
    return len(items), total


def cleanup_stray_payload(target: Path | None = None,
                          keep_user_data: bool = True) -> tuple[int, int, list[str]]:
    """删除 exe 旁边的残留文件。

    keep_user_data=True 时保留可能含用户产物的目录（output / videos / models / .venv），
    只清掉脚本与缓存类内容。

    返回 (删除条目数, 释放字节数, 错误信息列表)。
    """
    protected = {"output", "videos", "models", ".venv"} if keep_user_data else set()
    removed = 0
    freed = 0
    errors: list[str] = []
    for p in stray_payload_files(target):
        if p.name in protected:
            continue
        try:
            size = (p.stat().st_size if p.is_file()
                    else sum(f.stat().st_size for f in p.rglob("*") if f.is_file()))
        except OSError:
            size = 0
        try:
            if p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p)
            removed += 1
            freed += size
        except Exception as exc:
            errors.append(f"{p.name}: {exc}")
    return removed, freed, errors


def open_in_explorer(path: Path | str) -> None:
    """在资源管理器中打开目录，或选中文件。"""
    p = Path(path)
    try:
        if sys.platform == "win32":
            if p.is_dir():
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                os.system(f'explorer /select,"{p}"')
        elif sys.platform == "darwin":
            os.system(f'open "{p if p.is_dir() else p.parent}"')
        else:
            os.system(f'xdg-open "{p if p.is_dir() else p.parent}"')
    except Exception:
        pass


def open_file(path: Path | str) -> None:
    """用系统默认程序打开文件。"""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception:
        pass


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
