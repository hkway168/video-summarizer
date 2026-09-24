"""
gui/tabs/base.py —— 功能页基类

统一处理：
    * 同一页面同时只跑一个后台任务
    * 任务运行期间禁用操作按钮 / 显示进度条
    * 与主窗口（App）的交互入口
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from ..core.runner import BaseTask, run_task
from ..widgets.console import ConsoleWidget
from ..widgets.theme import CONTENT_PAD


class BaseTab(ttk.Frame):
    #: 页面标题（主窗口顶部显示）
    title = ""
    subtitle = ""

    def __init__(self, parent: tk.Misc, app):
        # 左右边距与主窗口标题栏、状态栏共用 CONTENT_PAD，保证左边界对齐
        super().__init__(parent, padding=(CONTENT_PAD, 12, CONTENT_PAD, 12))
        self.app = app
        self.cfg = app.cfg
        self.console: Optional[ConsoleWidget] = None
        self.progress: Optional[ttk.Progressbar] = None
        self._task: Optional[BaseTask] = None
        self._buttons: list[ttk.Button] = []
        self._loaded = False

    # ── 与 App 的交互 ───────────────────────────────────────────────────
    def python_path(self) -> str:
        return self.app.python_path()

    def require_python(self) -> Optional[str]:
        py = self.python_path()
        if not py:
            messagebox.showwarning(
                "未找到 Python",
                "尚未配置可用的 Python 解释器。\n\n"
                "请到「环境安装」页选择解释器，或点击「下载便携版 Python」一键安装。",
                parent=self,
            )
            self.app.show_page("env")
            return None
        return py

    # ── 任务管理 ────────────────────────────────────────────────────────
    def register_buttons(self, *buttons: ttk.Button) -> None:
        for b in buttons:
            if b not in self._buttons:
                self._buttons.append(b)

    @property
    def busy(self) -> bool:
        return bool(self._task and self._task.running)

    def start_task(self, task: BaseTask, on_done: Callable[[int], None] | None = None,
                   banner: bool = True) -> bool:
        if self.busy:
            messagebox.showinfo("请稍候", "当前页面已有任务在运行，请等待完成或先点击「停止」。",
                                parent=self)
            return False
        if self.console is None:
            return False
        self._task = task
        if banner and task.title:
            self.console.stamp(f"▶ {task.title}")
        self.set_busy(True)

        def _done(code: int) -> None:
            self.set_busy(False)
            if self.console:
                if code == 0:
                    self.console.stamp(f"✔ {task.title} 完成", "success")
                elif code == 130:
                    self.console.stamp(f"■ {task.title} 已取消", "warn")
                else:
                    self.console.stamp(f"✖ {task.title} 失败（退出码 {code}）", "error")
                    for line in self._failure_hints(task, code):
                        self.console.append(f"    💡 {line}", "warn")
            if on_done:
                on_done(code)

        run_task(self, task, self.console, on_done=_done)
        return True

    @staticmethod
    def _failure_hints(task: BaseTask, code: int) -> list[str]:
        """根据退出码与输出，给出可操作的排错建议。"""
        text = "\n".join(getattr(task, "output", []) or []).lower()
        hints: list[str] = []
        if code == 127:
            hints.append("找不到可执行文件：请到「环境安装」页重新选择 Python 解释器。")
        if "unicodedecodeerror" in text or "codec can't decode" in text:
            hints.append("依赖清单编码异常：请确认 requirements*.txt 为 UTF-8（带 BOM）。")
        if "could not find a version" in text or "no matching distribution" in text:
            hints.append("镜像源里找不到该包：试着把「pip 镜像」切到官方源或阿里源后重试。")
        if ("connection" in text or "timed out" in text or "timeout" in text
                or "retries exceeded" in text):
            hints.append("网络连接失败：检查网络/代理，或更换「pip 镜像」后重试。")
        if "permission denied" in text or "access is denied" in text or "winerror 5" in text:
            hints.append("权限不足：建议点「创建独立虚拟环境 (.venv)」后再装，或以管理员身份运行。")
        if "no module named pip" in text or "no module named ensurepip" in text:
            hints.append("该解释器没有 pip：请改用便携版 Python 或先点体检表里的 pip 行修复。")
        if "disk" in text and "space" in text:
            hints.append("磁盘空间不足：清理磁盘后重试。")
        if not hints:
            hints.append("请查看上方红色日志定位原因；仍不行可点「保存日志」留存后反馈。")
        return hints

    def stop_task(self) -> None:
        if self._task and self._task.running:
            self._task.cancel()
            if self.console:
                self.console.append("⏹ 正在停止...", "warn")

    def set_busy(self, flag: bool) -> None:
        state = "disabled" if flag else "normal"
        for b in self._buttons:
            try:
                b.configure(state=state)
            except tk.TclError:
                pass
        if self.progress is not None:
            try:
                if flag:
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(18)
                else:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
            except tk.TclError:
                pass

    # ── 生命周期 ────────────────────────────────────────────────────────
    def on_show(self) -> None:
        """页面被切到前台时调用（子类可重写）。"""
        if not self._loaded:
            self._loaded = True
            self.on_first_show()

    def on_first_show(self) -> None:
        pass
