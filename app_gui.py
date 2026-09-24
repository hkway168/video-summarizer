"""
app_gui.py —— 视频转写助手 图形界面入口

开发态运行：
    python app_gui.py

打包成 exe：
    python build_exe.py

界面提供四块能力：
    ① 环境安装   —— Python 运行时 / 依赖 / FFmpeg / Deno / 扫码登录，一键装好
    ② 视频转写   —— 平台选择 + 链接输入 + 参数 + 实时日志
    ③ 模型管理   —— Whisper 模型列表 / 下载（支持镜像、断点续传）/ 删除
    ④ 输出管理   —— 文字稿列表、预览、复制给 AI、另存、删除
"""
from __future__ import annotations

import io
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _ensure_import_path() -> None:
    """保证无论从哪个目录启动，都能 import gui 包。"""
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _fix_streams() -> None:
    """打包成无控制台窗口的 exe 后 sys.stdout 为 None，写日志防止 print 崩溃。"""
    log_path = None
    try:
        from gui.core import paths  # noqa: WPS433 - 需要先修好 sys.path
        log_path = paths.logs_dir() / "gui.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_path = Path(os.environ.get("TEMP", ".")) / "video-summarizer-gui.log"

    if sys.stdout is None or sys.stderr is None:
        try:
            fh = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
            fh.write(f"\n===== GUI 启动 {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            if sys.stdout is None:
                sys.stdout = fh
            if sys.stderr is None:
                sys.stderr = fh
        except Exception:
            sys.stdout = sys.stdout or io.StringIO()
            sys.stderr = sys.stderr or io.StringIO()
    else:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except Exception:
                pass


def _enable_dpi_awareness() -> None:
    """高分屏下避免界面模糊。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # type: ignore[attr-defined]
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()          # type: ignore[attr-defined]
    except Exception:
        pass


def _fatal(message: str) -> None:
    """启动期致命错误：尽量用弹窗告知用户。"""
    sys.stderr.write(message + "\n")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("视频转写助手 启动失败", message)
        root.destroy()
    except Exception:
        pass


def main() -> int:
    _ensure_import_path()
    _fix_streams()
    _enable_dpi_awareness()

    try:
        import tkinter  # noqa: F401
    except Exception as exc:
        _fatal("当前 Python 缺少 tkinter 组件，无法显示界面。\n\n"
               "请安装带 tkinter 的官方 Python（Windows 安装包默认自带），"
               f"或改用打包好的 exe。\n\n原始错误：{exc}")
        return 1

    def _hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        try:
            from tkinter import messagebox
            messagebox.showerror("出现未处理的错误",
                                 f"{exc_type.__name__}: {exc}\n\n详细信息已写入 logs/gui.log")
        except Exception:
            pass

    sys.excepthook = _hook

    try:
        from gui.app import main as run_app
    except Exception as exc:
        _fatal(f"加载界面模块失败：{exc}\n\n{traceback.format_exc()}")
        return 1

    start_page = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--page="):
            start_page = arg.split("=", 1)[1].strip()

    run_app(selftest="--selftest" in sys.argv, start_page=start_page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
