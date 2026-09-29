"""
gui/tabs/download_tab.py —— 「只下载」页

只把视频/音频文件拉到本地，完全不碰 Whisper：
    * 平台选择 + 链接输入（多行批量）
    * 视频 / 仅音频、画质上限、封装格式、音频格式
    * 可选：一起下字幕（srt）、封面图、写入元数据
    * 下载完成后可直接「送去本地转写」，形成两段式流程
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import jobs, paths
from ..core.runner import ProcessTask
from ..widgets.console import ConsoleWidget
from ..widgets.theme import COLORS, FONTS, card, card_title
from .base import BaseTab

PLATFORMS = [
    ("自动识别（推荐）", "auto"),
    ("YouTube", "youtube"),
    ("哔哩哔哩", "bilibili"),
    ("抖音", "douyin"),
]

KINDS = [
    ("视频（画面 + 声音）", "video"),
    ("仅音频", "audio"),
]

QUALITIES = [
    ("最佳画质（不限）", "best"),
    ("最高 2160p (4K)", "2160"),
    ("最高 1440p (2K)", "1440"),
    ("最高 1080p", "1080"),
    ("最高 720p", "720"),
    ("最高 480p", "480"),
    ("最高 360p", "360"),
    ("最小体积", "worst"),
]

CONTAINERS = [
    ("MP4（兼容性最好）", "mp4"),
    ("MKV（保留多轨）", "mkv"),
    ("保持原始格式", "keep"),
]

AUDIO_FORMATS = [
    ("保持原始（最快）", "keep"),
    ("MP3", "mp3"),
    ("M4A", "m4a"),
    ("WAV", "wav"),
    ("FLAC", "flac"),
]

BROWSERS = [
    ("Chrome", "chrome"), ("Edge", "edge"), ("Firefox", "firefox"),
    ("Brave", "brave"), ("不读取浏览器 cookies", "none"),
]

# 抖音「列表型批量」：一个入口展开成一堆视频（独立于上方链接下载）
DY_BATCHES = [
    ("对方个人主页的全部作品", "post"),
    ("喜欢（点赞）列表", "like"),
    ("本人的收藏", "collection"),
    ("本人的某个收藏夹", "collects"),
]

# 每种批量类型对「目标」输入框的要求
DY_TARGET_SPEC = {
    "post": ("对方主页", "粘贴对方主页链接，如 https://www.douyin.com/user/MS4wLjABAAAA…", True),
    "like": ("谁的点赞", "留空 = 本人；填对方主页链接 = 下载对方公开的点赞", False),
    "collection": ("无需填写", "收藏列表只能读取本人账号，无需填写", False),
    "collects": ("收藏夹 ID", "点右侧「列出我的收藏夹」获取 ID", True),
}

DY_LIMITS = [
    ("不限（全部）", 0),
    ("最新 10 个", 10),
    ("最新 30 个", 30),
    ("最新 50 个", 50),
    ("最新 100 个", 100),
    ("最新 200 个", 200),
]

URL_PLACEHOLDER = ("在这里粘贴要下载的视频链接，一行一个可批量下载，例如：\n"
                   "https://www.bilibili.com/video/BV1xx411c7mD/\n"
                   "https://www.youtube.com/watch?v=xxxxxxxxxxx\n"
                   "抖音主页链接会自动展开为该用户的全部作品：\n"
                   "https://www.douyin.com/user/MS4wLjABAAAA...")


def _labels(pairs) -> list[str]:
    return [p[0] for p in pairs]


def _value_of(pairs, label: str, default: str) -> str:
    for text, val in pairs:
        if text == label:
            return val
    return default


def _label_of(pairs, value: str) -> str:
    for text, val in pairs:
        if val == value:
            return text
    return pairs[0][0]


class DownloadTab(BaseTab):
    title = "视频下载"
    subtitle = "只下载、不转写：把视频或音频原样保存到本地，可选一起下载字幕和封面"

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, app)
        self.last_files: list[str] = []
        self._build()
        self._load_config()
        self._sync_kind()

    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        top = card(self)
        top.pack(fill="x")
        card_title(top, "视频链接",
                   "支持 YouTube / 哔哩哔哩 / 抖音。下载得到的是原始媒体文件，不会生成文字稿。"
                   ).pack(fill="x", anchor="w")

        box = tk.Frame(top, bg=COLORS["card"], highlightthickness=1,
                       highlightbackground=COLORS["border"])
        box.pack(fill="x", pady=(10, 6))
        self.url_text = tk.Text(box, height=4, wrap="none", bd=0, relief="flat",
                                font=FONTS.get("mono", ("Consolas", 10)),
                                padx=10, pady=8, undo=True)
        self.url_text.pack(fill="x")
        self.url_text.bind("<FocusIn>", self._clear_placeholder)
        self.url_text.bind("<FocusOut>", self._maybe_placeholder)
        self.url_text.bind("<KeyRelease>", lambda e: self._update_count())
        self._placeholder_on = False
        self._show_placeholder()

        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill="x")
        self.btn_start = ttk.Button(row, text="⬇ 开始下载", style="Accent.TButton",
                                    command=self._start)
        self.btn_start.pack(side="left")
        ttk.Button(row, text="■ 停止", style="Danger.TButton",
                   command=self.stop_task).pack(side="left", padx=(8, 0))
        b_clear = ttk.Button(row, text="清空链接", command=self._clear_urls)
        b_clear.pack(side="left", padx=(8, 0))
        b_paste = ttk.Button(row, text="从转写页带入链接", command=self._pull_from_transcribe)
        b_paste.pack(side="left", padx=(8, 0))
        self.count_var = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.count_var, style="Muted.TLabel").pack(side="left", padx=(12, 0))
        self.register_buttons(self.btn_start, b_clear, b_paste)

        self._build_douyin_batch()

        # ── 下载参数 ──
        opt = card(self)
        opt.pack(fill="x", pady=(12, 0))
        card_title(opt, "下载参数").pack(fill="x", anchor="w")
        grid = ttk.Frame(opt, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))

        def add(r: int, col: int, label: str, widget: tk.Widget, hint: str = "") -> None:
            ttk.Label(grid, text=label, style="Card.TLabel").grid(
                row=r, column=col * 3, sticky="w", padx=(0, 8), pady=5)
            widget.grid(row=r, column=col * 3 + 1, sticky="ew", pady=5, padx=(0, 16))
            if hint:
                ttk.Label(grid, text=hint, style="Muted.TLabel").grid(
                    row=r, column=col * 3 + 2, sticky="w", padx=(0, 18), pady=5)

        self.kind_var = tk.StringVar()
        cb_kind = ttk.Combobox(grid, textvariable=self.kind_var, state="readonly",
                               values=_labels(KINDS), width=24)
        cb_kind.bind("<<ComboboxSelected>>", lambda e: self._sync_kind())
        add(0, 0, "下载内容", cb_kind)

        self.quality_var = tk.StringVar()
        self.cb_quality = ttk.Combobox(grid, textvariable=self.quality_var, state="readonly",
                                       values=_labels(QUALITIES), width=24)
        add(1, 0, "画质上限", self.cb_quality)

        self.container_var = tk.StringVar()
        self.cb_container = ttk.Combobox(grid, textvariable=self.container_var, state="readonly",
                                         values=_labels(CONTAINERS), width=24)
        add(2, 0, "视频格式", self.cb_container)

        self.platform_var = tk.StringVar()
        cb_plat = ttk.Combobox(grid, textvariable=self.platform_var, state="readonly",
                               values=_labels(PLATFORMS), width=22)
        add(0, 1, "平台", cb_plat)

        self.browser_var = tk.StringVar()
        cb_browser = ttk.Combobox(grid, textvariable=self.browser_var, state="readonly",
                                  values=_labels(BROWSERS), width=22)
        add(1, 1, "cookies 来源", cb_browser)

        self.audio_fmt_var = tk.StringVar()
        self.cb_audio_fmt = ttk.Combobox(grid, textvariable=self.audio_fmt_var, state="readonly",
                                         values=_labels(AUDIO_FORMATS), width=22)
        add(2, 1, "音频格式", self.cb_audio_fmt, "转码需要 FFmpeg")

        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(4, weight=1)

        chk = ttk.Frame(opt, style="Card.TFrame")
        chk.pack(fill="x", pady=(8, 0))
        self.subs_var = tk.BooleanVar()
        self.thumb_var = tk.BooleanVar()
        self.meta_var = tk.BooleanVar()
        self.open_dir_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(chk, text="同时下载字幕（srt）", variable=self.subs_var).pack(side="left")
        ttk.Checkbutton(chk, text="下载封面图", variable=self.thumb_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(chk, text="写入标题/作者元数据", variable=self.meta_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(chk, text="完成后打开目录", variable=self.open_dir_var).pack(side="left", padx=(16, 0))

        orow = ttk.Frame(opt, style="Card.TFrame")
        orow.pack(fill="x", pady=(10, 0))
        ttk.Label(orow, text="保存目录", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.out_var = tk.StringVar()
        ttk.Entry(orow, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        b_pick = ttk.Button(orow, text="更改…", width=8, command=self._pick_output)
        b_pick.pack(side="left", padx=(6, 0))
        b_open = ttk.Button(orow, text="打开", width=6,
                            command=lambda: paths.open_in_explorer(self._output_dir()))
        b_open.pack(side="left", padx=(6, 0))
        self.register_buttons(b_pick, b_open)

        # ── 完成后动作 ──
        after = card(self)
        after.pack(fill="x", pady=(12, 0))
        arow = ttk.Frame(after, style="Card.TFrame")
        arow.pack(fill="x")
        ttk.Label(arow, text="下载完成后：", style="H2.TLabel").pack(side="left")
        self.btn_to_local = ttk.Button(arow, text="📁 把刚下载的文件送去「本地转写」",
                                       command=self._send_to_local, state="disabled")
        self.btn_to_local.pack(side="left", padx=(10, 0))
        self.after_var = tk.StringVar(value="（还没有下载记录）")
        ttk.Label(after, textvariable=self.after_var, style="Muted.TLabel",
                  wraplength=940, justify="left").pack(anchor="w", pady=(8, 0))

        self.console = ConsoleWidget(self, title="下载日志", height=10)
        self.console.pack(fill="both", expand=True, pady=(12, 0))
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))

    # ══════════════════════════════════════════════════════════════════
    def _build_douyin_batch(self) -> None:
        """「抖音批量」区块：把主页 / 收藏 / 喜欢 展开成一堆视频再逐个下载。"""
        box = card(self)
        box.pack(fill="x", pady=(12, 0))
        card_title(box, "抖音批量下载",
                   "与上方链接下载互相独立：选好来源点本区的「开始批量下载」，就能把对方主页全部作品、"
                   "或自己的收藏 / 喜欢一次性下完。需要先在「环境安装」页扫码登录抖音。"
                   ).pack(fill="x", anchor="w")

        r1 = ttk.Frame(box, style="Card.TFrame")
        r1.pack(fill="x", pady=(10, 0))
        ttk.Label(r1, text="批量来源", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.dy_batch_var = tk.StringVar()
        cb_batch = ttk.Combobox(r1, textvariable=self.dy_batch_var, state="readonly",
                                values=_labels(DY_BATCHES), width=26)
        cb_batch.bind("<<ComboboxSelected>>", lambda e: self._sync_douyin())
        cb_batch.pack(side="left")

        ttk.Label(r1, text="数量", style="Card.TLabel").pack(side="left", padx=(16, 8))
        self.dy_limit_var = tk.StringVar()
        self.cb_dy_limit = ttk.Combobox(r1, textvariable=self.dy_limit_var, state="readonly",
                                        values=_labels(DY_LIMITS), width=14)
        self.cb_dy_limit.pack(side="left")

        ttk.Label(r1, text="翻页间隔", style="Card.TLabel").pack(side="left", padx=(16, 8))
        self.dy_interval_var = tk.StringVar(value="8")
        self.sp_dy_interval = ttk.Spinbox(r1, from_=0, to=60, width=5,
                                          textvariable=self.dy_interval_var)
        self.sp_dy_interval.pack(side="left")
        ttk.Label(r1, text="秒（太小容易被风控）", style="Muted.TLabel").pack(side="left", padx=(6, 0))

        r2 = ttk.Frame(box, style="Card.TFrame")
        r2.pack(fill="x", pady=(8, 0))
        self.dy_target_label = ttk.Label(r2, text="目标", style="Card.TLabel", width=9)
        self.dy_target_label.pack(side="left", padx=(0, 8))
        self.dy_target_var = tk.StringVar()
        self.ent_dy_target = ttk.Entry(r2, textvariable=self.dy_target_var)
        self.ent_dy_target.pack(side="left", fill="x", expand=True)
        self.btn_dy_collects = ttk.Button(r2, text="列出我的收藏夹", width=15,
                                          command=self._list_collects)
        self.btn_dy_collects.pack(side="left", padx=(6, 0))

        self.dy_hint_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.dy_hint_var, style="Muted.TLabel",
                  wraplength=940, justify="left").pack(anchor="w", pady=(6, 0))

        r3 = ttk.Frame(box, style="Card.TFrame")
        r3.pack(fill="x", pady=(8, 0))
        self.dy_images_var = tk.BooleanVar()
        self.dy_overwrite_var = tk.BooleanVar()
        ttk.Checkbutton(r3, text="包含图文/图集作品", variable=self.dy_images_var).pack(side="left")
        ttk.Checkbutton(r3, text="已存在的文件也重新下载", variable=self.dy_overwrite_var
                        ).pack(side="left", padx=(16, 0))
        self.dy_subdir_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="每个批量任务单独建文件夹", variable=self.dy_subdir_var
                        ).pack(side="left", padx=(16, 0))
        ttk.Label(r3, text="如 抖音_某某_主页作品 / 抖音_本人收藏 / 抖音_收藏夹_名称",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        r4 = ttk.Frame(box, style="Card.TFrame")
        r4.pack(fill="x", pady=(10, 0))
        self.btn_dy_start = ttk.Button(r4, text="⬇ 开始批量下载", style="Accent.TButton",
                                       command=self._start_batch)
        self.btn_dy_start.pack(side="left")
        ttk.Button(r4, text="■ 停止", style="Danger.TButton",
                   command=self.stop_task).pack(side="left", padx=(8, 0))
        self.btn_dy_preview = ttk.Button(r4, text="👁 先预览清单（不下载）",
                                         command=self._preview_batch)
        self.btn_dy_preview.pack(side="left", padx=(8, 0))
        ttk.Label(r4, text="画质 / 格式 / 保存目录沿用下方「下载参数」",
                  style="Muted.TLabel").pack(side="left", padx=(12, 0))
        self.register_buttons(self.btn_dy_collects, self.btn_dy_start, self.btn_dy_preview)

    def set_busy(self, flag: bool) -> None:
        self._is_busy = flag
        super().set_busy(flag)
        if not flag:
            self._sync_douyin()

    def _dy_batch(self) -> str:
        return _value_of(DY_BATCHES, self.dy_batch_var.get(), "post")

    def _dy_limit(self) -> int:
        for text, val in DY_LIMITS:
            if text == self.dy_limit_var.get():
                return int(val)
        return 0

    def _dy_interval(self) -> int:
        try:
            return max(0, int(self.dy_interval_var.get() or 8))
        except ValueError:
            return 8

    def _sync_douyin(self) -> None:
        """按批量来源启停「目标」输入框，并给出对应提示。"""
        batch = self._dy_batch()
        label, hint, required = DY_TARGET_SPEC.get(batch, ("", "", False))

        self.dy_target_label.configure(text=label or "目标")
        self.ent_dy_target.configure(state="disabled" if batch == "collection" else "normal")
        if not getattr(self, "_is_busy", False):
            self.btn_dy_collects.configure(
                state="normal" if batch == "collects" else "disabled")

        if batch == "like":
            self.dy_hint_var.set(
                f"{hint}\n⚠️ 抖音默认隐藏点赞列表：下载本人的需先在 APP「我 → 设置 → "
                "隐私设置 → 点赞列表」改为公开；对方未公开时读不到。")
        elif batch == "collection":
            self.dy_hint_var.set(f"{hint}\n收藏接口只靠 cookie 鉴权，因此只能是当前登录的本人账号。")
        else:
            self.dy_hint_var.set(hint + ("　（必填）" if required else ""))

    # ── 占位符 / 链接 ──────────────────────────────────────────────────
    def _show_placeholder(self) -> None:
        self.url_text.delete("1.0", "end")
        self.url_text.insert("1.0", URL_PLACEHOLDER)
        self.url_text.configure(foreground=COLORS["muted"])
        self._placeholder_on = True

    def _clear_placeholder(self, _e=None) -> None:
        if self._placeholder_on:
            self.url_text.delete("1.0", "end")
            self.url_text.configure(foreground=COLORS["text"])
            self._placeholder_on = False

    def _maybe_placeholder(self, _e=None) -> None:
        if not self.url_text.get("1.0", "end").strip():
            self._show_placeholder()

    def _clear_urls(self) -> None:
        self.url_text.delete("1.0", "end")
        self._placeholder_on = False
        self._show_placeholder()
        self._update_count()

    def _urls(self) -> list[str]:
        if self._placeholder_on:
            return []
        out: list[str] = []
        for line in self.url_text.get("1.0", "end").splitlines():
            line = line.strip().strip('"')
            if not line or line.startswith("#"):
                continue
            m = re.search(r"https?://\S+", line)
            out.append(m.group(0) if m else line)
        return out

    def _update_count(self) -> None:
        n = len(self._urls())
        self.count_var.set(f"已识别 {n} 个链接" if n else "")

    def set_urls(self, urls: list[str]) -> None:
        """供其它页面注入链接。"""
        self._clear_placeholder()
        self.url_text.delete("1.0", "end")
        self.url_text.insert("1.0", "\n".join(urls))
        self._update_count()

    def _pull_from_transcribe(self) -> None:
        page = self.app.pages.get("transcribe")
        urls = page._urls() if page is not None else []   # type: ignore[attr-defined]
        if not urls:
            messagebox.showinfo("没有链接", "「视频转写」页当前没有填写链接。", parent=self)
            return
        self.set_urls(urls)
        self.console.append(f"↔ 已从「视频转写」页带入 {len(urls)} 个链接", "info")

    # ── 配置 ──────────────────────────────────────────────────────────
    def _load_config(self) -> None:
        self.kind_var.set(_label_of(KINDS, self.cfg.get("dl_kind")))
        self.quality_var.set(_label_of(QUALITIES, self.cfg.get("dl_quality")))
        self.container_var.set(_label_of(CONTAINERS, self.cfg.get("dl_container")))
        self.audio_fmt_var.set(_label_of(AUDIO_FORMATS, self.cfg.get("dl_audio_format")))
        self.platform_var.set(_label_of(PLATFORMS, self.cfg.get("platform")))
        self.browser_var.set(_label_of(BROWSERS, self.cfg.get("browser")))
        self.subs_var.set(bool(self.cfg.get("dl_subs")))
        self.thumb_var.set(bool(self.cfg.get("dl_thumbnail")))
        self.meta_var.set(bool(self.cfg.get("dl_embed_metadata")))
        self.out_var.set(str(self.cfg.download_dir()))
        last = self.cfg.get("dl_urls") or ""
        if last.strip():
            self.set_urls([l for l in last.splitlines() if l.strip()])

        # 抖音批量
        self.dy_batch_var.set(_label_of(DY_BATCHES, self.cfg.get("dy_batch") or "post"))
        self.dy_limit_var.set(_label_of(DY_LIMITS, int(self.cfg.get("dy_limit") or 0)))
        self.dy_target_var.set(str(self.cfg.get("dy_target") or ""))
        self.dy_interval_var.set(str(self.cfg.get("dy_page_interval") or 8))
        self.dy_images_var.set(bool(self.cfg.get("dy_include_images")))
        self.dy_overwrite_var.set(bool(self.cfg.get("dy_overwrite")))
        self.dy_subdir_var.set(bool(self.cfg.get("dy_batch_subdir", True)))
        self._sync_douyin()

    def save_config(self) -> None:
        self.cfg.update({
            "dl_kind": _value_of(KINDS, self.kind_var.get(), "video"),
            "dl_quality": _value_of(QUALITIES, self.quality_var.get(), "best"),
            "dl_container": _value_of(CONTAINERS, self.container_var.get(), "mp4"),
            "dl_audio_format": _value_of(AUDIO_FORMATS, self.audio_fmt_var.get(), "keep"),
            "dl_subs": bool(self.subs_var.get()),
            "dl_thumbnail": bool(self.thumb_var.get()),
            "dl_embed_metadata": bool(self.meta_var.get()),
            "dl_output_dir": self.out_var.get().strip(),
            "dl_urls": "\n".join(self._urls()),
            "dy_batch": self._dy_batch(),
            "dy_target": self.dy_target_var.get().strip(),
            "dy_limit": self._dy_limit(),
            "dy_page_interval": self._dy_interval(),
            "dy_include_images": bool(self.dy_images_var.get()),
            "dy_overwrite": bool(self.dy_overwrite_var.get()),
            "dy_batch_subdir": bool(self.dy_subdir_var.get()),
        })

    def _sync_kind(self) -> None:
        """按「视频 / 仅音频」启停相关下拉框。"""
        audio = _value_of(KINDS, self.kind_var.get(), "video") == "audio"
        for w in (self.cb_quality, self.cb_container):
            w.configure(state="disabled" if audio else "readonly")
        self.cb_audio_fmt.configure(state="readonly" if audio else "disabled")

    def _output_dir(self) -> Path:
        raw = self.out_var.get().strip()
        return Path(raw) if raw else paths.videos_dir()

    def _pick_output(self) -> None:
        d = filedialog.askdirectory(title="选择保存目录",
                                    initialdir=str(self._output_dir()), parent=self)
        if d:
            self.out_var.set(d)
            self.cfg.set("dl_output_dir", d)

    def on_first_show(self) -> None:
        self.console.append("💡 这里只做下载，不会转写、也不消耗显卡。", "step")
        self.console.append("    · 想连带生成文字稿 → 用「视频转写」页的一键流程；", "muted")
        self.console.append("    · 先下载、之后再转写 → 下载完点「送去本地转写」。", "muted")
        self.console.append("📱 抖音批量：粘贴对方主页链接会自动展开全部作品；"
                            "自己的收藏 / 喜欢用下方「抖音批量下载」选择，并点该区的「开始批量下载」。", "step")
        self.console.append("    · 需先在「环境安装」页扫码登录抖音；"
                            "量大时建议先点「先预览清单」确认。", "muted")

    # ══════════════════════════════════════════════════════════════════
    def _preflight(self) -> bool:
        rep = self.app.report
        if rep is None:
            return True
        yt = rep.get("yt_dlp")
        if yt and yt.status != "ok":
            if messagebox.askyesno(
                "缺少核心依赖",
                "下载需要 yt-dlp，当前未安装。\n\n现在去「环境安装」页一键安装吗？",
                parent=self,
            ):
                self.app.show_page("env")
            return False
        ff = rep.get("ffmpeg")
        kind = _value_of(KINDS, self.kind_var.get(), "video")
        need_ff = kind == "audio" and _value_of(AUDIO_FORMATS, self.audio_fmt_var.get(), "keep") != "keep"
        need_ff = need_ff or (kind == "video" and _value_of(CONTAINERS, self.container_var.get(), "mp4") != "keep")
        if need_ff and ff and ff.status != "ok":
            if not messagebox.askyesno(
                "缺少 FFmpeg",
                "选择的格式需要 FFmpeg 做合流/转码，但未检测到它。\n\n"
                "仍要继续吗？（点「否」去安装；也可把格式改成「保持原始」）",
                parent=self,
            ):
                self.app.show_page("env")
                return False
        return True

    def _validate_douyin(self) -> bool:
        """抖音批量的前置校验：必填目标 + cookies 是否就绪。"""
        batch = self._dy_batch()
        target = self.dy_target_var.get().strip()
        _, _, required = DY_TARGET_SPEC.get(batch, ("", "", False))
        if required and not target:
            messagebox.showinfo(
                "还缺一点信息",
                "「对方个人主页的全部作品」需要填主页链接，"
                "「本人的某个收藏夹」需要填收藏夹 ID。\n\n"
                "收藏夹 ID 可以点「列出我的收藏夹」获取。",
                parent=self)
            return False
        if not paths.douyin_logged_in():
            if messagebox.askyesno(
                "抖音还没登录",
                "抖音批量下载需要本人登录态（douyin_cookies.json），当前没找到有效 cookies。\n\n"
                "现在去「环境安装」页扫码登录吗？",
                parent=self,
            ):
                self.app.show_page("env")
            return False
        return True

    def _common_ready(self) -> str | None:
        """两种下载方式共用的前置检查，返回 Python 路径；不通过返回 None。"""
        if self.busy:
            messagebox.showinfo("请稍候", "当前页面已有任务在运行，请等待完成或先点击「停止」。",
                                parent=self)
            return None
        py = self.require_python()
        if not py:
            return None
        if not paths.project_ready():
            messagebox.showerror("缺少程序文件",
                                 f"找不到 media_downloader.py：\n{paths.runtime_dir()}", parent=self)
            return None
        return py

    def _media_kwargs(self) -> dict:
        """「下载参数」卡片里的通用选项。"""
        return dict(
            kind=_value_of(KINDS, self.kind_var.get(), "video"),
            quality=_value_of(QUALITIES, self.quality_var.get(), "best"),
            container=_value_of(CONTAINERS, self.container_var.get(), "mp4"),
            audio_format=_value_of(AUDIO_FORMATS, self.audio_fmt_var.get(), "keep"),
            subs=bool(self.subs_var.get()),
            thumbnail=bool(self.thumb_var.get()),
            embed_metadata=bool(self.meta_var.get()),
            platform=_value_of(PLATFORMS, self.platform_var.get(), "auto"),
            browser=_value_of(BROWSERS, self.browser_var.get(), "chrome"),
        )

    def _start(self) -> None:
        """上方「视频链接」的下载：只处理链接框里的链接，与抖音批量无关。"""
        urls = self._urls()
        if not urls:
            messagebox.showinfo("请输入链接", "请先在上方粘贴至少一个视频链接。", parent=self)
            return
        py = self._common_ready()
        if not py or not self._preflight():
            return

        self.save_config()
        out_dir = self._output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        task = jobs.download_task(py, urls, out_dir, **self._media_kwargs())
        self.console.append(f"⬇ 下载 {len(urls)} 个链接", "step")
        self.console.append(f"📁 保存到：{out_dir}", "step")
        self.start_task(task, on_done=lambda code: self._on_finished(task, code))

    def _start_batch(self, list_only: bool = False) -> None:
        """「抖音批量下载」区块独立的下载入口：不读取上方链接框。"""
        py = self._common_ready()
        if not py or not self._validate_douyin():
            return
        if not list_only and not self._preflight():
            return

        self.save_config()
        out_dir = self._output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        batch = self._dy_batch()
        task = jobs.download_task(
            py, [], out_dir,
            **self._media_kwargs(),
            dy_batch=batch,
            dy_target=self.dy_target_var.get().strip(),
            limit=self._dy_limit(),
            page_interval=self._dy_interval(),
            include_images=bool(self.dy_images_var.get()),
            overwrite=bool(self.dy_overwrite_var.get()),
            list_only=list_only,
            batch_subdir=bool(self.dy_subdir_var.get()),
            batch_folder=self._custom_batch_folder(batch),
        )

        n = self._dy_limit()
        self.console.append(
            f"{'👁 预览' if list_only else '⬇ 批量下载'}抖音「{_label_of(DY_BATCHES, batch)}」"
            f"，数量上限：{n or '不限'}", "step")
        self.console.append(
            "    抖音列表接口有频率限制，翻页之间会等待几秒，请耐心等待…", "muted")
        if not list_only:
            tail = "（本批次会自动建子文件夹）" if self.dy_subdir_var.get() else ""
            self.console.append(f"📁 保存到：{out_dir}{tail}", "step")
        self.start_task(task, on_done=lambda code: self._on_finished(task, code))

    def _preview_batch(self) -> None:
        """只展开列表、不下载，让用户先确认数量和内容。"""
        self._start_batch(list_only=True)

    def _custom_batch_folder(self, batch: str) -> str:
        """收藏夹用名称命名子目录（名称来自「列出我的收藏夹」的缓存）；其余交给后端自动命名。"""
        if batch != "collects" or not self.dy_subdir_var.get():
            return ""
        cid = self.dy_target_var.get().strip()
        names = self.cfg.get("dy_collects_names") or {}
        name = str(names.get(cid) or "").strip() if isinstance(names, dict) else ""
        return f"抖音_收藏夹_{name}" if name else ""

    def _list_collects(self) -> None:
        """读取本人收藏夹列表，供填写收藏夹 ID。"""
        py = self.require_python()
        if not py:
            return
        if not paths.douyin_logged_in():
            if messagebox.askyesno(
                "抖音还没登录",
                "读取收藏夹需要本人登录态，现在去「环境安装」页扫码登录吗？",
                parent=self,
            ):
                self.app.show_page("env")
            return
        task = jobs.douyin_collects_task(py)
        self.console.append("📂 正在读取本人收藏夹列表…", "step")
        self.start_task(task, on_done=lambda code: self._on_collects(task, code))

    def _on_collects(self, task: ProcessTask, code: int) -> None:
        data = jobs.parse_result(task.output) or {}
        folders = data.get("collects") or []
        if not folders:
            self.console.append("⚠️ 没读到收藏夹，请查看上方日志（可能是登录态已失效）。", "warn")
            return
        names = dict(self.cfg.get("dy_collects_names") or {})
        for f in folders:
            cid, name = str(f.get("collects_id") or ""), str(f.get("name") or "").strip()
            if cid and name:
                names[cid] = name
        self.cfg.update({"dy_collects_names": names})
        self.console.append(f"📂 共 {len(folders)} 个收藏夹：", "success")
        for f in folders:
            self.console.append(
                f"    · {f.get('name') or '(未命名)'}  {f.get('total', 0)} 个作品\n"
                f"      ID: {f.get('collects_id')}", "info")
        # 只有一个就直接填上，省得用户复制
        if len(folders) == 1:
            self.dy_target_var.set(str(folders[0].get("collects_id") or ""))
            self.console.append("    已自动填入上方「收藏夹 ID」。", "muted")
        else:
            self.console.append("    复制想要的 ID 填到上方「收藏夹 ID」即可。", "muted")

    # ══════════════════════════════════════════════════════════════════
    def _on_finished(self, task: ProcessTask, code: int) -> None:
        data = jobs.parse_result(task.output)
        if not data:
            if code not in (0, 130):
                self.console.append("⚠️ 未获取到结构化结果，请查看上方日志。", "warn")
            return

        ok = data.get("success") or []
        errs = data.get("errors") or []
        listing = data.get("listing") or []
        batch_dirs = [d for d in (data.get("batch_dirs") or []) if d]
        self.last_files = [r.get("file_path", "") for r in ok if r.get("file_path")]
        if batch_dirs:
            self.console.append("📂 本批次子文件夹：", "step")
            for d in batch_dirs:
                self.console.append(f"    · {d}", "info")

        # ── 预览模式：只展示展开出来的清单 ──
        if not ok and listing:
            self.console.append(f"👁 展开出 {len(listing)} 个视频（未下载）：", "success")
            for i, e in enumerate(listing[:40], 1):
                title = (e.get("title") or "").replace("\n", " ")[:46]
                who = e.get("uploader") or ""
                when = e.get("create_time") or ""
                self.console.append(
                    f"    {i:>3}. {('@' + who + '  ') if who else ''}{when}  {title}", "info")
            if len(listing) > 40:
                self.console.append(f"    … 其余 {len(listing) - 40} 个略", "muted")
            self.console.append("    确认无误后点「抖音批量下载」里的「⬇ 开始批量下载」即可。", "muted")

        if ok:
            total = sum(r.get("file_size", 0) for r in ok)
            skipped = sum(1 for r in ok if r.get("skipped"))
            tail_skip = f"（其中 {skipped} 个已存在被跳过）" if skipped else ""
            self.console.append(
                f"✅ 成功下载 {len(ok)} 个文件，合计 {paths.fmt_bytes(total)}{tail_skip}：",
                "success")
            for r in ok:
                extra = r.get("extra_files") or []
                tail = f"  (+{len(extra)} 个附件)" if extra else ""
                mark = "⏭" if r.get("skipped") else "·"
                self.console.append(
                    f"    {mark} {r.get('title')}  {paths.fmt_bytes(r.get('file_size', 0))}{tail}\n"
                    f"      → {r.get('file_path')}", "success")
            self.btn_to_local.configure(state="normal")
            self.after_var.set(f"最近下载 {len(self.last_files)} 个文件，可点左侧按钮直接转写它们。")
            if self.open_dir_var.get():
                # 单个批量子目录时直接打开它，否则打开总目录
                target = Path(batch_dirs[0]) if len(batch_dirs) == 1 else self._output_dir()
                paths.open_in_explorer(target if target.is_dir() else self._output_dir())

        for e in errs:
            if e.get("error_type") == "batch_expand_failed":
                self.console.append(f"❌ 批量展开失败：{e.get('url')}\n    {e.get('error')}",
                                    "error")
            elif e.get("error_type") == "login_required":
                platform = e.get("platform", "")
                self.console.append(f"⚠️ {platform} 需要登录：{e.get('reason')}", "warn")
                if messagebox.askyesno(
                    "需要登录",
                    f"{platform} 需要登录后才能下载该视频。\n\n是否前往「环境安装」页扫码登录？",
                    parent=self,
                ):
                    self.app.show_page("env")
            else:
                self.console.append(f"❌ {e.get('url')}\n    {e.get('error')}", "error")

    def _send_to_local(self) -> None:
        files = [f for f in self.last_files if Path(f).is_file()]
        if not files:
            messagebox.showinfo("没有可用文件", "最近下载的文件已不存在，请重新下载。", parent=self)
            return
        page = self.app.pages.get("local")
        if page is None:
            return
        page.add_files(files, clear=True)     # type: ignore[attr-defined]
        self.app.show_page("local")
