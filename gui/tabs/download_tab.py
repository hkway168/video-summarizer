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

URL_PLACEHOLDER = ("在这里粘贴要下载的视频链接，一行一个可批量下载，例如：\n"
                   "https://www.bilibili.com/video/BV1xx411c7mD/\n"
                   "https://www.youtube.com/watch?v=xxxxxxxxxxx")


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

    def _start(self) -> None:
        urls = self._urls()
        if not urls:
            messagebox.showinfo("请输入链接", "请先粘贴至少一个视频链接。", parent=self)
            return
        py = self.require_python()
        if not py:
            return
        if not paths.project_ready():
            messagebox.showerror("缺少程序文件",
                                 f"找不到 media_downloader.py：\n{paths.runtime_dir()}", parent=self)
            return
        if not self._preflight():
            return

        self.save_config()
        out_dir = self._output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        task = jobs.download_task(
            py, urls, out_dir,
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
        self.console.append(f"⬇ 共 {len(urls)} 个链接，保存到：{out_dir}", "step")
        self.start_task(task, on_done=lambda code: self._on_finished(task, code))

    # ══════════════════════════════════════════════════════════════════
    def _on_finished(self, task: ProcessTask, code: int) -> None:
        data = jobs.parse_result(task.output)
        if not data:
            if code not in (0, 130):
                self.console.append("⚠️ 未获取到结构化结果，请查看上方日志。", "warn")
            return

        ok = data.get("success") or []
        errs = data.get("errors") or []
        self.last_files = [r.get("file_path", "") for r in ok if r.get("file_path")]

        if ok:
            total = sum(r.get("file_size", 0) for r in ok)
            self.console.append(f"✅ 成功下载 {len(ok)} 个文件，合计 {paths.fmt_bytes(total)}：", "success")
            for r in ok:
                extra = r.get("extra_files") or []
                tail = f"  (+{len(extra)} 个附件)" if extra else ""
                self.console.append(
                    f"    · {r.get('title')}  {paths.fmt_bytes(r.get('file_size', 0))}{tail}\n"
                    f"      → {r.get('file_path')}", "success")
            self.btn_to_local.configure(state="normal")
            self.after_var.set(f"最近下载 {len(self.last_files)} 个文件，可点左侧按钮直接转写它们。")
            if self.open_dir_var.get():
                paths.open_in_explorer(self._output_dir())

        for e in errs:
            if e.get("error_type") == "login_required":
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
