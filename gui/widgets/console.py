"""
gui/widgets/console.py —— 日志控制台控件

特性：
    * 彩色分级输出（info / success / warn / error / cmd / step）
    * 支持 transient 行（\\r 进度条会原地刷新，不会刷屏）
    * 自动滚动开关、清空、保存日志
    * 超长自动截断，避免内存膨胀
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

from .theme import COLORS, FONTS

MAX_LINES = 5000


class ConsoleWidget(ttk.Frame):
    def __init__(self, parent: tk.Misc, title: str = "运行日志", height: int = 12,
                 show_toolbar: bool = True):
        super().__init__(parent, style="Card.TFrame", padding=(12, 10, 12, 12))
        self._transient_active = False
        self.autoscroll = tk.BooleanVar(value=True)

        if show_toolbar:
            bar = ttk.Frame(self, style="Card.TFrame")
            bar.pack(fill="x", pady=(0, 6))
            ttk.Label(bar, text=title, style="H2.TLabel").pack(side="left")
            ttk.Button(bar, text="清空", width=6, command=self.clear).pack(side="right")
            ttk.Button(bar, text="保存日志", width=9,
                       command=self.save_to_file).pack(side="right", padx=(0, 6))
            ttk.Checkbutton(bar, text="自动滚动", variable=self.autoscroll,
                            style="TCheckbutton").pack(side="right", padx=(0, 10))

        wrap = tk.Frame(self, bg=COLORS["console_bg"], highlightthickness=1,
                        highlightbackground=COLORS["border"])
        wrap.pack(fill="both", expand=True)

        self.text = tk.Text(
            wrap, height=height, wrap="word", bd=0, relief="flat",
            bg=COLORS["console_bg"], fg=COLORS["console_fg"],
            insertbackground=COLORS["console_fg"],
            selectbackground="#2b3a55", font=FONTS.get("mono", ("Consolas", 9)),
            padx=10, pady=8, state="disabled",
        )
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        self.text.tag_configure("info", foreground=COLORS["console_fg"])
        self.text.tag_configure("success", foreground="#4ade80")
        self.text.tag_configure("warn", foreground="#fbbf24")
        self.text.tag_configure("error", foreground="#fb7185")
        self.text.tag_configure("cmd", foreground="#67a8ff")
        self.text.tag_configure("step", foreground="#c4b5fd")
        self.text.tag_configure("muted", foreground="#7b879c")

    # ── 输出 ────────────────────────────────────────────────────────────
    def append(self, text: str, level: str = "info", transient: bool = False) -> None:
        if text is None:
            return
        txt = self.text
        txt.configure(state="normal")
        if self._transient_active:
            try:
                txt.delete("tmark", "end-1c")
            except tk.TclError:
                pass
            self._transient_active = False
        txt.mark_set("tmark", "end-1c")
        txt.mark_gravity("tmark", "left")
        txt.insert("end", text.rstrip("\n") + "\n", level if level in
                   ("info", "success", "warn", "error", "cmd", "step", "muted") else "info")
        self._transient_active = bool(transient)
        self._trim()
        txt.configure(state="disabled")
        if self.autoscroll.get():
            txt.see("end")

    def log(self, text: str, level: str = "info") -> None:
        self.append(text, level)

    def stamp(self, text: str, level: str = "step") -> None:
        self.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", level)

    def blank(self) -> None:
        self.append("", "info")

    # ── 维护 ────────────────────────────────────────────────────────────
    def _trim(self) -> None:
        try:
            lines = int(self.text.index("end-1c").split(".")[0])
            if lines > MAX_LINES:
                self.text.delete("1.0", f"{lines - MAX_LINES}.0")
        except Exception:
            pass

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._transient_active = False

    def get_all(self) -> str:
        return self.text.get("1.0", "end-1c")

    def save_to_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存日志",
            defaultextension=".log",
            initialfile=f"video-summarizer-{datetime.now():%Y%m%d-%H%M%S}.log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("全部文件", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(self.get_all(), encoding="utf-8")
            self.append(f"✅ 日志已保存到 {path}", "success")
        except Exception as exc:
            self.append(f"❌ 日志保存失败：{exc}", "error")
