"""
gui/widgets/theme.py —— 统一配色、字体与 ttk 样式
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

COLORS = {
    "bg": "#f3f5f9",
    "card": "#ffffff",
    "border": "#e2e7ef",
    "text": "#1b2330",
    "muted": "#78849b",
    "accent": "#2f6fed",
    "accent_hover": "#2259c9",
    "accent_light": "#e8f0ff",
    "success": "#12a150",
    "warn": "#d98207",
    "error": "#e0484d",
    "sidebar": "#151a24",
    "sidebar_fg": "#aeb8ca",
    "sidebar_hover": "#222936",
    "sidebar_active": "#2f6fed",
    "console_bg": "#0f141e",
    "console_fg": "#cbd3e1",
    "row_alt": "#f8fafc",
}

_FAMILY = "Microsoft YaHei UI"
_MONO = "Consolas"
_EMOJI = "Segoe UI Emoji"

FONTS: dict[str, tuple] = {}

#: 界面缩放系数（跟随系统 DPI，96dpi = 1.0）
SCALE = 1.0

#: 内容区左右边距。页面标题、卡片、底部状态栏统一用它，保证左边界在一条线上
CONTENT_PAD = 16
#: 卡片内部留白
CARD_PAD = 14


def scaled(px: int) -> int:
    """把按 96dpi 设计的像素值换算到当前 DPI。"""
    return max(1, int(round(px * SCALE)))


def _pick_family(root: tk.Misc) -> None:
    global _FAMILY, _MONO, _EMOJI
    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()
    for cand in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "PingFang SC", "Segoe UI"):
        if cand in families:
            _FAMILY = cand
            break
    for cand in ("Cascadia Mono", "Consolas", "Courier New"):
        if cand in families:
            _MONO = cand
            break
    # 图标专用字体：emoji 交给专门字体渲染，宽度更稳定
    for cand in ("Segoe UI Emoji", "Segoe UI Symbol", "Apple Color Emoji", _FAMILY):
        if cand in families:
            _EMOJI = cand
            break
    FONTS.update({
        "ui": (_FAMILY, 10),
        "ui_small": (_FAMILY, 9),
        "ui_bold": (_FAMILY, 10, "bold"),
        "h1": (_FAMILY, 16, "bold"),
        "h2": (_FAMILY, 12, "bold"),
        "nav": (_FAMILY, 11),
        "nav_bold": (_FAMILY, 11, "bold"),
        "nav_icon": (_EMOJI, 12),
        "brand": (_FAMILY, 13, "bold"),
        "brand_icon": (_EMOJI, 14),
        "mono": (_MONO, 9),
        "mono_small": (_MONO, 8),
    })


def apply_theme(root: tk.Misc) -> ttk.Style:
    global SCALE
    _pick_family(root)
    try:
        SCALE = max(1.0, min(2.0, float(root.winfo_fpixels("1i")) / 96.0))
    except Exception:
        SCALE = 1.0
    c = COLORS
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    try:
        root.option_add("*Font", FONTS["ui"])
    except Exception:
        pass

    style.configure(".", font=FONTS["ui"], background=c["bg"], foreground=c["text"])
    style.configure("TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["card"], relief="flat")
    style.configure("Console.TFrame", background=c["console_bg"])

    style.configure("TLabel", background=c["bg"], foreground=c["text"])
    style.configure("Card.TLabel", background=c["card"], foreground=c["text"])
    style.configure("H1.TLabel", background=c["bg"], foreground=c["text"], font=FONTS["h1"])
    style.configure("H2.TLabel", background=c["card"], foreground=c["text"], font=FONTS["h2"])
    style.configure("Muted.TLabel", background=c["card"], foreground=c["muted"], font=FONTS["ui_small"])
    style.configure("MutedBg.TLabel", background=c["bg"], foreground=c["muted"], font=FONTS["ui_small"])
    style.configure("Ok.TLabel", background=c["card"], foreground=c["success"], font=FONTS["ui_bold"])
    style.configure("Warn.TLabel", background=c["card"], foreground=c["warn"], font=FONTS["ui_bold"])
    style.configure("Err.TLabel", background=c["card"], foreground=c["error"], font=FONTS["ui_bold"])
    style.configure("Status.TLabel", background=c["card"], foreground=c["muted"], font=FONTS["ui_small"])

    # 普通按钮
    style.configure("TButton", background="#eef1f6", foreground=c["text"],
                    borderwidth=1, focusthickness=0, padding=(12, 6), relief="flat")
    style.map("TButton",
              background=[("disabled", "#f2f4f8"), ("pressed", "#dde3ec"), ("active", "#e4e9f2")],
              foreground=[("disabled", "#aab2c2")],
              bordercolor=[("!disabled", c["border"])])

    # 主操作按钮
    style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff",
                    borderwidth=0, padding=(14, 7), relief="flat", font=FONTS["ui_bold"])
    style.map("Accent.TButton",
              background=[("disabled", "#b9cbf3"), ("pressed", c["accent_hover"]),
                          ("active", c["accent_hover"])],
              foreground=[("disabled", "#f0f4ff")])

    # 危险按钮
    style.configure("Danger.TButton", background="#fdecec", foreground=c["error"],
                    borderwidth=1, padding=(12, 6), relief="flat")
    style.map("Danger.TButton",
              background=[("disabled", "#faf1f1"), ("active", "#f9dcdc")],
              bordercolor=[("!disabled", "#f3c9c9")])

    style.configure("TCheckbutton", background=c["card"], foreground=c["text"])
    style.map("TCheckbutton", background=[("active", c["card"])])
    style.configure("BgCheck.TCheckbutton", background=c["bg"], foreground=c["text"])

    style.configure("TEntry", fieldbackground="#ffffff", foreground=c["text"],
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                    insertcolor=c["text"], padding=5)
    style.map("TEntry", bordercolor=[("focus", c["accent"])])

    style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff",
                    foreground=c["text"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"], arrowcolor=c["muted"],
                    padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f4f6f9")],
              bordercolor=[("focus", c["accent"])])

    style.configure("TSeparator", background=c["border"])
    style.configure("TProgressbar", background=c["accent"], troughcolor="#e8ecf3",
                    bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])

    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                    foreground=c["text"], bordercolor=c["border"], borderwidth=0,
                    rowheight=scaled(28), font=FONTS["ui"])
    style.map("Treeview",
              background=[("selected", c["accent_light"])],
              foreground=[("selected", c["text"])])
    style.configure("Treeview.Heading", background="#f7f9fc", foreground=c["muted"],
                    relief="flat", font=FONTS["ui_small"], padding=(6, 6))
    style.map("Treeview.Heading", background=[("active", "#eef2f8")])

    style.configure("Vertical.TScrollbar", background="#e6eaf1", troughcolor=c["bg"],
                    bordercolor=c["bg"], arrowcolor=c["muted"], width=12)
    style.configure("Horizontal.TScrollbar", background="#e6eaf1", troughcolor=c["bg"],
                    bordercolor=c["bg"], arrowcolor=c["muted"])
    return style


def card(parent: tk.Misc, padding: int = CARD_PAD, **kw) -> ttk.Frame:
    """带白底的卡片容器。"""
    f = ttk.Frame(parent, style="Card.TFrame", padding=padding, **kw)
    return f


def hline(parent: tk.Misc) -> ttk.Separator:
    return ttk.Separator(parent, orient="horizontal")


def card_title(parent: tk.Misc, text: str, subtitle: str = "") -> ttk.Frame:
    box = ttk.Frame(parent, style="Card.TFrame")
    ttk.Label(box, text=text, style="H2.TLabel").pack(anchor="w")
    if subtitle:
        ttk.Label(box, text=subtitle, style="Muted.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(2, 0))
    return box
