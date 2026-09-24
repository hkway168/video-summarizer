"""
gui/widgets/scrollable.py —— 纵向滚动容器

用于内容较长的页面（如「环境安装」）。把子控件放到 .body 里即可。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import COLORS


class VScrollFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0,
                                background=COLORS["bg"])
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, on: bool) -> None:
        if on:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        try:
            if self.canvas.bbox("all") and self.canvas.winfo_height() >= self.canvas.bbox("all")[3]:
                return
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass
