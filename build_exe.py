"""
build_exe.py —— 把图形界面打包成独立 exe

用法：
    python build_exe.py                 # 单文件 exe（默认，推荐）
    python build_exe.py --onedir        # 目录形式（启动更快）
    python build_exe.py --console       # 保留控制台窗口（排错用）
    python build_exe.py --clean-only    # 只清理 build/dist 产物

产物：
    dist/VideoSummarizer.exe  → 同时复制一份到项目根目录

说明：
    * exe 里只打包「界面本身」（纯 tkinter + 标准库），体积约 10~15MB
    * yt-dlp / faster-whisper 等重依赖不打包，由界面上的「环境安装」页按需安装，
      这样既能保证 exe 小、又能随时升级 yt-dlp 应对平台风控
    * main.py / providers/ / scripts/ 等脚本会作为 payload 一起打包，
      exe 被单独拷到别处时会自动释放，保证能用
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "VideoSummarizer"
ENTRY = ROOT / "app_gui.py"
DIST = ROOT / "dist"
WORK = ROOT / "build"

# 需要随 exe 一起分发的项目脚本（解压后放到 payload/ 下）
PAYLOAD_FILES = [
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
]
PAYLOAD_DIRS = ["providers", "scripts"]

EXCLUDES = [
    "numpy", "pandas", "scipy", "matplotlib", "PIL", "torch", "yt_dlp",
    "faster_whisper", "huggingface_hub", "playwright", "rich", "pytest",
]


if sys.platform == "win32":  # 避免 GBK 控制台打印 emoji 时崩溃
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def log(msg: str) -> None:
    try:
        print(f"[build] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[build] {msg.encode('ascii', 'replace').decode('ascii')}", flush=True)


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        log("PyInstaller 已就绪")
        return
    except ImportError:
        pass
    log("未检测到 PyInstaller，正在安装...")
    cmd = [sys.executable, "-m", "pip", "install", "pyinstaller",
           "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    if subprocess.call(cmd) != 0:
        log("PyInstaller 安装失败，请手动执行：pip install pyinstaller")
        raise SystemExit(1)


def clean() -> None:
    for d in (DIST, WORK):
        if d.exists():
            log(f"清理 {d}")
            shutil.rmtree(d, ignore_errors=True)
    spec = ROOT / f"{NAME}.spec"
    if spec.exists():
        spec.unlink()


def data_args() -> list[str]:
    args: list[str] = []
    for name in PAYLOAD_FILES:
        src = ROOT / name
        if src.exists():
            args += ["--add-data", f"{src};payload"]
        else:
            log(f"⚠ 缺少 {name}，跳过")
    for name in PAYLOAD_DIRS:
        src = ROOT / name
        if src.exists():
            args += ["--add-data", f"{src};payload/{name}"]
    return args


def build(onefile: bool, console: bool) -> Path:
    ensure_pyinstaller()
    clean()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        "--onefile" if onefile else "--onedir",
        "--console" if console else "--windowed",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "tkinter.font",
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args += data_args()
    args.append(str(ENTRY))

    log("开始打包：" + " ".join(args[2:]))
    if subprocess.call(args) != 0:
        log("❌ 打包失败")
        raise SystemExit(2)

    exe = DIST / (f"{NAME}.exe" if onefile else f"{NAME}/{NAME}.exe")
    if not exe.exists():
        log(f"❌ 未找到产物 {exe}")
        raise SystemExit(3)
    size = exe.stat().st_size / 1024 / 1024
    log(f"✅ 打包完成：{exe}  ({size:.1f} MB)")

    if onefile:
        target = ROOT / f"{NAME}.exe"
        try:
            shutil.copy2(exe, target)
            log(f"✅ 已复制到项目根目录：{target}")
            log("   直接双击它即可运行（建议保持和 main.py 同一目录）")
        except Exception as exc:
            log(f"⚠ 复制到根目录失败（可能正在运行）：{exc}")
    return exe


def main() -> None:
    ap = argparse.ArgumentParser(description="打包视频转写助手 GUI 为 exe")
    ap.add_argument("--onedir", action="store_true", help="打包成目录形式（启动更快）")
    ap.add_argument("--console", action="store_true", help="保留控制台窗口（排错用）")
    ap.add_argument("--clean-only", action="store_true", help="只清理构建产物")
    a = ap.parse_args()

    if a.clean_only:
        clean()
        log("已清理")
        return
    build(onefile=not a.onedir, console=a.console)


if __name__ == "__main__":
    main()
