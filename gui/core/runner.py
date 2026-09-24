"""
gui/core/runner.py —— 后台任务运行器

界面上所有耗时操作（pip 安装、模型下载、视频转写、文件下载）都走这里：
    * 任务在独立线程执行，绝不阻塞 tkinter 主循环
    * 输出通过 queue 传回，由主线程 pump() 消费后写入控制台控件
    * 支持中途取消（子进程走 taskkill /T 杀掉整棵进程树）

用法：
    task = ProcessTask([py, "main.py", url], cwd=runtime)
    run_task(widget, task, console, on_done=lambda code: ...)
"""
from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

# Windows 进程创建标志
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

_SEP_RE = re.compile(rb"[\r\n]")

#: ProcessTask.output 最多保留多少行（防止长任务撑爆内存；结构化结果在末尾，不受影响）
MAX_COLLECT_LINES = 800

LineCallback = Callable[[str, str, bool], None]   # (text, level, transient)
DoneCallback = Callable[[int], None]


class BaseTask:
    """任务基类：子类实现 _run() 返回退出码。"""

    def __init__(self, title: str = ""):
        self.title = title
        self._q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._cancelled = threading.Event()
        self.exit_code: Optional[int] = None
        self.on_line: Optional[LineCallback] = None
        self.on_done: Optional[DoneCallback] = None
        self.started_at: float = 0.0

    # ── 供子类使用 ──────────────────────────────────────────────────────
    def emit(self, text: str, level: str = "info", transient: bool = False) -> None:
        self._q.put(("line", (text, level, transient)))

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _run(self) -> int:  # pragma: no cover - 抽象
        raise NotImplementedError

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self, on_line: LineCallback | None = None,
              on_done: DoneCallback | None = None) -> None:
        self.on_line = on_line
        self.on_done = on_done
        self._running = True
        self._cancelled.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._safe_run, daemon=True)
        self._thread.start()

    def _safe_run(self) -> None:
        code = 1
        try:
            code = int(self._run() or 0)
        except Exception as exc:  # 任务内部异常统一转成日志 + 非零退出码
            self.emit(f"❌ 任务异常: {exc}", "error")
            code = 1
        finally:
            self._q.put(("done", code))

    def pump(self) -> bool:
        """在 tkinter 主线程里调用：消费队列，返回任务是否仍在运行。"""
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                text, level, transient = payload  # type: ignore[misc]
                if self.on_line:
                    try:
                        self.on_line(text, level, transient)
                    except Exception:
                        pass
            elif kind == "done":
                self._running = False
                self.exit_code = int(payload)  # type: ignore[arg-type]
                if self.on_done:
                    try:
                        self.on_done(self.exit_code)
                    except Exception:
                        pass
        return self._running

    @property
    def running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self._cancelled.set()


class FuncTask(BaseTask):
    """把一个普通 python 函数跑到后台。

    函数签名：fn(task: FuncTask) -> int | None
    函数内部用 task.emit(...) 输出日志、用 task.cancelled 检查取消。
    """

    def __init__(self, fn: Callable[["FuncTask"], Optional[int]], title: str = ""):
        super().__init__(title)
        self._fn = fn

    def _run(self) -> int:
        return int(self._fn(self) or 0)


class ProcessTask(BaseTask):
    """运行一个子进程并实时回传输出（stdout + stderr 合并）。"""

    def __init__(
        self,
        argv: Sequence[str],
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        title: str = "",
        echo_cmd: bool = True,
        collect: bool = True,      # 默认收集输出：失败时可据此给出排错建议
    ):
        super().__init__(title or " ".join(str(a) for a in argv[:2]))
        self.argv = [str(a) for a in argv]
        self.cwd = str(cwd) if cwd else None
        self.env = env
        self.echo_cmd = echo_cmd
        self.collect = collect
        self.output: list[str] = []
        self._proc: Optional[subprocess.Popen] = None

    # ── 内部 ────────────────────────────────────────────────────────────
    def _creationflags(self) -> int:
        if sys.platform != "win32":
            return 0
        return CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

    def _run(self) -> int:
        if self.echo_cmd:
            self.emit("$ " + subprocess.list2cmdline(self.argv), "cmd")
        try:
            self._proc = subprocess.Popen(
                self.argv,
                cwd=self.cwd,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                creationflags=self._creationflags(),
            )
        except FileNotFoundError:
            self.emit(f"❌ 找不到可执行文件: {self.argv[0]}", "error")
            return 127
        except Exception as exc:
            self.emit(f"❌ 启动失败: {exc}", "error")
            return 1

        assert self._proc.stdout is not None
        fd = self._proc.stdout.fileno()
        buf = b""
        watchdog = threading.Thread(target=self._watch_cancel, daemon=True)
        watchdog.start()

        while True:
            try:
                chunk = os.read(fd, 65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf += chunk.replace(b"\r\n", b"\n")
            while True:
                m = _SEP_RE.search(buf)
                if not m:
                    break
                raw, sep = buf[: m.start()], buf[m.start(): m.start() + 1]
                buf = buf[m.end():]
                self._emit_raw(raw, transient=(sep == b"\r"))
        if buf:
            self._emit_raw(buf, transient=False)

        try:
            code = self._proc.wait(timeout=15)
        except Exception:
            code = 1
        if self.cancelled:
            self.emit("⏹ 已被用户取消", "warn")
            return 130
        return code

    def _emit_raw(self, raw: bytes, transient: bool) -> None:
        text = raw.decode("utf-8", errors="replace").rstrip()
        if not text:
            return
        level = "info"
        low = text.lower()
        if text.startswith("❌") or "error:" in low or "traceback" in low:
            level = "error"
        elif text.startswith("⚠") or "warning" in low:
            level = "warn"
        elif text.startswith("✅") or text.startswith("🎉"):
            level = "success"
        if self.collect:
            self.output.append(text)
            if len(self.output) > MAX_COLLECT_LINES * 2:
                del self.output[:-MAX_COLLECT_LINES]
        self.emit(text, level, transient)

    def _watch_cancel(self) -> None:
        while self._proc and self._proc.poll() is None:
            if self._cancelled.wait(0.2):
                self._kill()
                return

    def _kill(self) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                proc.send_signal(signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def cancel(self) -> None:
        super().cancel()
        self._kill()


class TaskChain(BaseTask):
    """顺序执行多个任务，任一失败即中止（用于「一键安装全部」）。"""

    def __init__(self, factories: Iterable[Callable[[], BaseTask]], title: str = "",
                 stop_on_error: bool = True):
        super().__init__(title)
        self._factories = list(factories)
        self._stop_on_error = stop_on_error
        self._current: Optional[BaseTask] = None

    def _run(self) -> int:
        last = 0
        total = len(self._factories)
        for idx, factory in enumerate(self._factories, 1):
            if self.cancelled:
                self.emit("⏹ 已被用户取消", "warn")
                return 130
            task = factory()
            self._current = task
            # 让子任务直接往本任务的队列里写日志，无需额外桥接线程
            task._q = self._q                       # noqa: SLF001 - 有意复用队列
            self.emit(f"━━━ 步骤 {idx}/{total}：{task.title} ━━━", "step")
            try:
                last = int(task._run() or 0)        # noqa: SLF001
            except Exception as exc:
                self.emit(f"❌ {task.title} 异常: {exc}", "error")
                last = 1
            if last != 0 and self._stop_on_error:
                self.emit(f"⛔ 步骤「{task.title}」失败（退出码 {last}），已中止后续步骤", "error")
                return last
        return last

    def cancel(self) -> None:
        super().cancel()
        if self._current:
            self._current.cancel()


# ── tkinter 胶水 ────────────────────────────────────────────────────────
def run_task(
    widget,
    task: BaseTask,
    console,
    on_done: DoneCallback | None = None,
    interval: int = 60,
) -> BaseTask:
    """启动任务并把输出写入 console（ConsoleWidget），完成后回调 on_done。"""

    def _line(text: str, level: str, transient: bool) -> None:
        console.append(text, level, transient=transient)

    def _done(code: int) -> None:
        if on_done:
            on_done(code)

    task.start(on_line=_line, on_done=_done)

    def _poll() -> None:
        if task.pump():
            widget.after(interval, _poll)

    widget.after(interval, _poll)
    return task
