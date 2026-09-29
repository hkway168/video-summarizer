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

    def cuda_libs_missing(self) -> list[str]:
        """依据最近一次环境体检：有 CUDA 显卡但缺运行库时返回缺失的 DLL，否则空列表。

        还没体检过时返回空列表（交给运行时的 cuda_libs_missing 错误兜底）。
        """
        rep = self.app.report
        raw = getattr(rep, "raw", None) or {}
        if int(raw.get("cuda_devices", -1) or -1) <= 0:
            return []
        return list(raw.get("cuda_libs_missing") or [])

    def guide_cuda_install(self, missing: list[str] | None = None, failed: bool = False) -> bool:
        """引导用户安装 GPU 运行库。用户同意则跳到「环境安装」页开始安装，返回 True。"""
        missing = missing or []
        head = ("GPU 转写失败：缺少 CUDA 运行库。" if failed
                else "你选择了用 GPU 转写，但当前环境缺少 CUDA 运行库。")
        detail = f"\n缺少：{', '.join(missing)}" if missing else ""
        msg = (
            f"{head}{detail}\n\n"
            "需要安装 NVIDIA 的 cuBLAS / cuDNN 运行库（pip 包，约 2GB，\n"
            "无需安装 CUDA Toolkit），装好后 GPU 转写通常比 CPU 快 5～10 倍。\n\n"
            "是否现在前往「环境安装」页开始下载安装？\n"
            "（选「否」可以把「计算设备」改成「自动」或「CPU」继续使用）"
        )
        if not messagebox.askyesno("需要安装 GPU 运行库", msg, parent=self):
            return False
        self.app.show_page("env")
        env_page = self.app.pages.get("env")
        if env_page is not None and hasattr(env_page, "install_cuda_libs_when_idle"):
            env_page.install_cuda_libs_when_idle()
        return True

    def device_preflight(self, device: str) -> bool:
        """开始转写前检查计算设备。返回 False 表示不要启动任务。"""
        missing = self.cuda_libs_missing()
        if not missing or self.console is None:
            return True
        if device == "cuda":
            self.guide_cuda_install(missing)
            return False
        if device == "auto":
            self.console.append("ℹ️ 检测到 NVIDIA 显卡，但缺少 CUDA 运行库，本次将用 CPU 转写（较慢）。", "warn")
            self.console.append("    💡 到「环境安装」页点「⚡ 安装 GPU 运行库」即可启用 GPU 加速。", "info")
        return True

    def handle_cuda_libs_missing(self, errs: list[dict]) -> None:
        """转写任务因缺 CUDA 运行库失败后的统一处理（多条错误只弹一次窗）。"""
        if self.console is not None:
            for e in errs:
                self.console.append(f"❌ {e.get('url')}\n    GPU 转写缺少 CUDA 运行库", "error")
            guide = (errs[0].get("install_guide") or {}) if errs else {}
            if guide.get("command"):
                self.console.append(f"    💡 手动安装命令：{guide['command']}", "info")
        missing = list(errs[0].get("missing") or []) if errs else []
        self.guide_cuda_install(missing, failed=True)

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
