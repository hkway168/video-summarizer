"""
gui/app.py —— 主窗口

左侧导航 + 右侧内容区：
    环境安装 / 视频转写 / 模型管理 / 输出管理
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_NAME, APP_VERSION
from .core import env_manager, model_manager, outputs, paths
from .core.config import get_config
from .core.runner import FuncTask
from .widgets.theme import COLORS, CONTENT_PAD, FONTS, apply_theme, scaled
from .tabs.download_tab import DownloadTab
from .tabs.env_tab import EnvTab
from .tabs.local_tab import LocalTab
from .tabs.model_tab import ModelTab
from .tabs.output_tab import OutputTab
from .tabs.transcribe_tab import TranscribeTab

# (页面 key, 图标, 文字)
# 图标与文字分开存放：导航行用固定宽度的图标单元格，
# 这样不同 emoji 的字形宽度差异不会把文字挤歪。
NAV_ITEMS = [
    ("transcribe", "🎬", "一键转写"),
    ("download", "⬇", "视频下载"),
    ("local", "📁", "本地转写"),
    ("models", "📦", "模型管理"),
    ("outputs", "📄", "输出管理"),
    ("env", "⚙", "环境安装"),
]

#: 兼容旧引用（selftest / 其他模块按 (key, label) 遍历）
NAV = [(key, f"{icon}  {text}") for key, icon, text in NAV_ITEMS]

# 导航行尺寸（按 96dpi 设计，运行时经 scaled() 换算）
NAV_INDICATOR_W = 3     # 左侧选中指示条
NAV_GUTTER_W = 15       # 指示条与图标之间的留白
NAV_ICON_W = 30         # 图标单元格宽度（固定 → 文字必然对齐）
NAV_ROW_PADY = 10


class App(tk.Tk):
    def __init__(self, start_page: str = ""):
        super().__init__()
        self._start_page = start_page
        self.cfg = get_config()
        self.report: env_manager.EnvReport | None = None
        #: key → {"row", "indicator", "icon", "text"}
        self._nav_buttons: dict[str, dict] = {}
        self.pages: dict[str, object] = {}
        self._current = ""

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.configure(background=COLORS["bg"])
        apply_theme(self)                      # 先算出 DPI 缩放，再定尺寸
        self.minsize(scaled(1000), scaled(680))
        self._init_geometry()

        paths.ensure_dirs()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        start = self._start_page or self.cfg.get("last_page") or "transcribe"
        self.show_page(start if start in self.pages else "transcribe")
        self.after(300, self.refresh_status)

    def _init_geometry(self) -> None:
        """按屏幕大小选择合适窗口尺寸并居中（只记忆尺寸，不记忆位置）。"""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = scaled(1240), scaled(850)
        saved = str(self.cfg.get("window_geometry") or "")
        m = re.match(r"^(\d+)x(\d+)", saved)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        w = max(scaled(1000), min(w, sw - scaled(60)))
        h = max(scaled(680), min(h, sh - scaled(90)))
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2 - 20)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ══════════════════════════════════════════════════════════════════
    # 布局
    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── 左侧导航 ──
        side = tk.Frame(self, bg=COLORS["sidebar"], width=scaled(210))
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)

        brand = tk.Frame(side, bg=COLORS["sidebar"])
        brand.pack(fill="x", pady=(22, 18))
        # 品牌区也按导航的列宽对齐（指示条 + 留白 = 文字左边界）
        brand_pad = scaled(NAV_INDICATOR_W + NAV_GUTTER_W)
        bline = tk.Frame(brand, bg=COLORS["sidebar"])
        bline.pack(fill="x", padx=(brand_pad, scaled(12)))
        tk.Label(bline, text="🎬", bg=COLORS["sidebar"], fg="#ffffff",
                 font=FONTS["brand_icon"]).pack(side="left")
        tk.Label(bline, text=" 视频转写助手", bg=COLORS["sidebar"], fg="#ffffff",
                 font=FONTS["brand"]).pack(side="left")
        tk.Label(brand, text=f"v{APP_VERSION}  ·  本地运行，不花钱", bg=COLORS["sidebar"],
                 fg=COLORS["sidebar_fg"], font=FONTS["ui_small"], anchor="w").pack(
            fill="x", padx=(brand_pad, scaled(12)), pady=(4, 0))

        for key, icon, text in NAV_ITEMS:
            self._nav_buttons[key] = self._build_nav_row(side, key, icon, text)

        foot = tk.Frame(side, bg=COLORS["sidebar"])
        foot.pack(side="bottom", fill="x", padx=(brand_pad, scaled(12)), pady=16)
        self.side_status = tk.StringVar(value="环境状态：检测中…")
        tk.Label(foot, textvariable=self.side_status, bg=COLORS["sidebar"],
                 fg=COLORS["sidebar_fg"], font=FONTS["ui_small"], justify="left",
                 anchor="w", wraplength=scaled(175)).pack(fill="x")
        tk.Button(foot, text="打开数据目录", bd=0, relief="flat", cursor="hand2",
                  bg=COLORS["sidebar"], fg="#7f8da3", activebackground=COLORS["sidebar"],
                  activeforeground="#ffffff", font=FONTS["ui_small"], padx=0,
                  highlightthickness=0, anchor="w",
                  command=lambda: paths.open_in_explorer(paths.data_dir())).pack(
            fill="x", pady=(8, 0))

        # ── 右侧内容 ──
        main = ttk.Frame(self)
        main.grid(row=0, column=1, sticky="nsew")
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        header = ttk.Frame(main, padding=(CONTENT_PAD, 16, CONTENT_PAD, 6))
        header.grid(row=0, column=0, sticky="ew")
        self.h_title = tk.StringVar(value="")
        self.h_sub = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.h_title, style="H1.TLabel").pack(anchor="w")
        ttk.Label(header, textvariable=self.h_sub, style="MutedBg.TLabel",
                  wraplength=900, justify="left").pack(anchor="w", pady=(2, 0))

        container = ttk.Frame(main)
        container.grid(row=1, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self._container = container

        self.pages = {
            "transcribe": TranscribeTab(container, self),
            "download": DownloadTab(container, self),
            "local": LocalTab(container, self),
            "models": ModelTab(container, self),
            "outputs": OutputTab(container, self),
            "env": EnvTab(container, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")  # type: ignore[attr-defined]

        # ── 底部状态栏 ──
        bar = ttk.Frame(main, style="Card.TFrame", padding=(CONTENT_PAD, 6))
        bar.grid(row=2, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(bar, text=f"程序目录：{paths.runtime_dir()}",
                  style="Status.TLabel").pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    # 左侧导航行
    # ══════════════════════════════════════════════════════════════════
    def _build_nav_row(self, parent: tk.Misc, key: str, icon: str, text: str) -> dict:
        """一行导航 = [指示条][留白][固定宽图标][文字]。

        图标放在固定宽度的网格单元里并居中，因此不管 emoji 字形宽窄，
        文字的左边界始终一致，不会出现某一行被挤歪的情况。
        """
        row = tk.Frame(parent, bg=COLORS["sidebar"], cursor="hand2")
        row.pack(fill="x")
        row.columnconfigure(0, minsize=scaled(NAV_INDICATOR_W))
        row.columnconfigure(1, minsize=scaled(NAV_GUTTER_W))
        row.columnconfigure(2, minsize=scaled(NAV_ICON_W))
        row.columnconfigure(3, weight=1)

        pady = scaled(NAV_ROW_PADY)
        indicator = tk.Frame(row, bg=COLORS["sidebar"], width=scaled(NAV_INDICATOR_W))
        indicator.grid(row=0, column=0, sticky="nsw")
        icon_lbl = tk.Label(row, text=icon, bg=COLORS["sidebar"], fg=COLORS["sidebar_fg"],
                            font=FONTS["nav_icon"], cursor="hand2")
        icon_lbl.grid(row=0, column=2, pady=pady)
        text_lbl = tk.Label(row, text=text, bg=COLORS["sidebar"], fg=COLORS["sidebar_fg"],
                            font=FONTS["nav"], anchor="w", cursor="hand2")
        text_lbl.grid(row=0, column=3, sticky="w", pady=pady, padx=(scaled(2), 0))

        item = {"row": row, "indicator": indicator, "icon": icon_lbl, "text": text_lbl}
        for w in (row, icon_lbl, text_lbl):
            w.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
            w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
        return item

    def _paint_nav(self, key: str, state: str) -> None:
        """state ∈ {normal, hover, active}"""
        item = self._nav_buttons.get(key)
        if not item:
            return
        bg = {"normal": COLORS["sidebar"],
              "hover": COLORS["sidebar_hover"],
              "active": COLORS["sidebar_active"]}[state]
        fg = COLORS["sidebar_fg"] if state == "normal" else "#ffffff"
        font = FONTS["nav_bold"] if state == "active" else FONTS["nav"]
        ind = COLORS["sidebar_active"] if state == "active" else bg
        try:
            item["row"].configure(bg=bg)
            item["indicator"].configure(bg=ind)
            item["icon"].configure(bg=bg, fg=fg)
            item["text"].configure(bg=bg, fg=fg, font=font)
        except tk.TclError:
            pass

    def _nav_hover(self, key: str, on: bool) -> None:
        if key == self._current:
            return
        self._paint_nav(key, "hover" if on else "normal")

    # ══════════════════════════════════════════════════════════════════
    # 页面切换
    # ══════════════════════════════════════════════════════════════════
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self._current = key
        for k in self._nav_buttons:
            self._paint_nav(k, "active" if k == key else "normal")
        self.h_title.set(getattr(page, "title", ""))
        self.h_sub.set(getattr(page, "subtitle", ""))
        page.tkraise()          # type: ignore[attr-defined]
        try:
            page.on_show()      # type: ignore[attr-defined]
        except Exception as exc:
            self.status_var.set(f"页面初始化异常：{exc}")
        self.cfg.set("last_page", key)

    # ══════════════════════════════════════════════════════════════════
    # 供各页面调用
    # ══════════════════════════════════════════════════════════════════
    def python_path(self) -> str:
        return env_manager.resolve_python(self.cfg)

    def set_report(self, report: env_manager.EnvReport) -> None:
        self.report = report
        self.refresh_status(report)

    def notify_env_changed(self) -> None:
        self.refresh_status()

    def refresh_models(self) -> None:
        for key in ("transcribe", "local"):
            page = self.pages.get(key)
            if page is not None:
                try:
                    page.refresh_models()   # type: ignore[attr-defined]
                except Exception:
                    pass
        self.refresh_status()

    def refresh_outputs(self) -> None:
        page = self.pages.get("outputs")
        if page is not None:
            try:
                page.refresh()          # type: ignore[attr-defined]
            except Exception:
                pass
        self.refresh_status()

    def refresh_status(self, report: env_manager.EnvReport | None = None) -> None:
        rep = report or self.report
        py = self.python_path()
        parts: list[str] = []
        if rep and rep.python_ver:
            parts.append(f"Python {rep.python_ver}")
        elif py:
            parts.append("Python 已配置")
        else:
            parts.append("⚠ 未找到 Python")

        if rep:
            yt = rep.get("yt_dlp")
            parts.append("yt-dlp " + ("✓" if yt and yt.status == "ok" else "✗"))
            ff = rep.get("ffmpeg")
            parts.append("FFmpeg " + ("✓" if ff and ff.status == "ok" else "✗"))
        else:
            parts.append("FFmpeg " + ("✓" if env_manager.find_ffmpeg() else "✗"))

        best = model_manager.best_local_model()
        parts.append(f"模型 {best}" if best else "模型 未下载")

        docs = outputs.scan_outputs(self.cfg.output_dir())
        parts.append(f"输出 {len(docs)} 篇")

        self.status_var.set("   ·   ".join(parts))
        self.side_status.set("环境状态\n" + "\n".join(parts))

    # ══════════════════════════════════════════════════════════════════
    def _on_close(self) -> None:
        busy = [k for k, p in self.pages.items() if getattr(p, "busy", False)]
        if busy and not messagebox.askyesno(
            "仍有任务在运行",
            "有后台任务（下载 / 安装 / 转写）尚未结束，强制退出会中断它们。\n\n确定退出吗？",
            parent=self,
        ):
            return
        for p in self.pages.values():
            try:
                p.stop_task()   # type: ignore[attr-defined]
            except Exception:
                pass
        try:
            self.cfg.set("window_geometry", f"{self.winfo_width()}x{self.winfo_height()}")
        except Exception:
            pass
        for key in ("transcribe", "download", "local"):
            page = self.pages.get(key)
            if page is None:
                continue
            try:
                page.save_config()   # type: ignore[attr-defined]
            except Exception:
                pass
        self.destroy()


def main(selftest: bool = False, start_page: str = "") -> None:
    app = App(start_page=start_page)
    if selftest:
        # 自检模式：把所有页面都渲染一遍后自动退出，用于打包前验证
        def _walk(index: int = 0) -> None:
            if index >= len(NAV_ITEMS):
                app.after(400, app.destroy)
                return
            app.show_page(NAV_ITEMS[index][0])
            app.after(500, lambda: _walk(index + 1))

        app.after(400, _walk)
    app.mainloop()
