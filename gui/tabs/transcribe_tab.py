"""
gui/tabs/transcribe_tab.py —— 「一键转写」页（链接 → 文字稿，一步到位）

与另两条链路的分工：
    本页                 = 下载/抓字幕 + 转写，一步产出 transcript.md
    gui/tabs/download_tab = 只下载文件，不转写
    gui/tabs/local_tab    = 只转写本地文件，不联网

界面能力：
    * 平台选择（自动识别 / YouTube / 哔哩哔哩 / 抖音）
    * 链接输入框（支持一行一个批量粘贴）
    * 转写模式、Whisper 模型、语言、cookies 浏览器等参数
    * 实时日志 + 停止；结束后解析结构化结果，缺依赖/缺登录时给出跳转引导
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import jobs, model_manager, paths
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

MODES = [
    ("自动：字幕优先，没字幕再本地转写", "auto"),
    ("仅字幕：秒出，没字幕则失败", "subtitle-only"),
    ("仅 Whisper：忽略字幕，强制本地转写", "whisper-only"),
]

LANGUAGES = [
    ("自动检测", "auto"), ("中文", "zh"), ("英文", "en"),
    ("日文", "ja"), ("韩文", "ko"), ("法文", "fr"), ("德文", "de"),
]

BROWSERS = [
    ("Chrome", "chrome"), ("Edge", "edge"), ("Firefox", "firefox"),
    ("Brave", "brave"), ("不读取浏览器 cookies", "none"),
]

URL_PLACEHOLDER = ("在这里粘贴视频链接，一行一个可批量处理，例如：\n"
                   "https://www.bilibili.com/video/BV1xx411c7mD/\n"
                   "https://www.youtube.com/watch?v=xxxxxxxxxxx")


def _labels(pairs) -> list[str]:
    return [p[0] for p in pairs]


def _value_of(pairs, label: str, default: str) -> str:
    for text, val in pairs:
        if text == label:
            return val
    return default


def _label_of(pairs, value: str, default_index: int = 0) -> str:
    for text, val in pairs:
        if val == value:
            return text
    return pairs[default_index][0]


class TranscribeTab(BaseTab):
    title = "一键转写"
    subtitle = "粘贴链接 → 自动抓字幕或本地 Whisper 转写 → 生成可直接交给 AI 总结的 Markdown"

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, app)
        self._build()
        self._load_config()

    # ══════════════════════════════════════════════════════════════════
    # 界面
    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        # ── 链接输入 ──
        top = card(self)
        top.pack(fill="x")
        card_title(top, "视频链接",
                   "支持 YouTube / 哔哩哔哩 / 抖音。可一次粘贴多行链接批量转写。").pack(fill="x", anchor="w")

        box = tk.Frame(top, bg=COLORS["card"], highlightthickness=1,
                       highlightbackground=COLORS["border"])
        box.pack(fill="x", pady=(10, 6))
        self.url_text = tk.Text(box, height=4, wrap="none", bd=0, relief="flat",
                                font=FONTS.get("mono", ("Consolas", 10)),
                                padx=10, pady=8, undo=True)
        self.url_text.pack(fill="x")
        self.url_text.bind("<FocusIn>", self._clear_placeholder)
        self.url_text.bind("<FocusOut>", self._maybe_placeholder)
        self._placeholder_on = False
        self._show_placeholder()

        urow = ttk.Frame(top, style="Card.TFrame")
        urow.pack(fill="x")
        self.btn_start = ttk.Button(urow, text="▶ 开始转写", style="Accent.TButton",
                                    command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(urow, text="■ 停止", style="Danger.TButton",
                                   command=self.stop_task)
        self.btn_stop.pack(side="left", padx=(8, 0))
        b_clear = ttk.Button(urow, text="清空链接", command=self._clear_urls)
        b_clear.pack(side="left", padx=(8, 0))
        b_dl = ttk.Button(urow, text="⬇ 只下载不转写", command=self._send_to_download)
        b_dl.pack(side="left", padx=(8, 0))
        self.count_var = tk.StringVar(value="")
        ttk.Label(urow, textvariable=self.count_var, style="Muted.TLabel").pack(side="left", padx=(12, 0))
        self.register_buttons(self.btn_start, b_clear, b_dl)
        self.url_text.bind("<KeyRelease>", lambda e: self._update_count())

        # ── 参数 ──
        opt = card(self)
        opt.pack(fill="x", pady=(12, 0))
        card_title(opt, "转写参数").pack(fill="x", anchor="w")
        grid = ttk.Frame(opt, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))

        def add_row(r: int, col: int, label: str, widget: tk.Widget, hint: str = "") -> None:
            ttk.Label(grid, text=label, style="Card.TLabel").grid(
                row=r, column=col * 3, sticky="w", padx=(0, 8), pady=5)
            widget.grid(row=r, column=col * 3 + 1, sticky="ew", pady=5, padx=(0, 16))
            if hint:
                ttk.Label(grid, text=hint, style="Muted.TLabel").grid(
                    row=r, column=col * 3 + 2, sticky="w", padx=(8, 20), pady=5)

        self.platform_var = tk.StringVar()
        cb_plat = ttk.Combobox(grid, textvariable=self.platform_var, state="readonly",
                               values=_labels(PLATFORMS), width=28)
        add_row(0, 0, "平台", cb_plat)

        self.mode_var = tk.StringVar()
        cb_mode = ttk.Combobox(grid, textvariable=self.mode_var, state="readonly",
                               values=_labels(MODES), width=34)
        add_row(1, 0, "模式", cb_mode)

        self.model_var = tk.StringVar()
        self.cb_model = ttk.Combobox(grid, textvariable=self.model_var, state="readonly", width=28)
        add_row(2, 0, "模型", self.cb_model)

        self.lang_var = tk.StringVar()
        cb_lang = ttk.Combobox(grid, textvariable=self.lang_var, state="readonly",
                               values=_labels(LANGUAGES), width=16)
        add_row(0, 1, "语言", cb_lang)

        self.browser_var = tk.StringVar()
        cb_browser = ttk.Combobox(grid, textvariable=self.browser_var, state="readonly",
                                  values=_labels(BROWSERS), width=22)
        add_row(1, 1, "cookies 来源", cb_browser)

        self.sub_lang_var = tk.StringVar()
        e_sub = ttk.Entry(grid, textvariable=self.sub_lang_var, width=18)
        add_row(2, 1, "字幕语言", e_sub, "可留空，如 zh-Hans / en")

        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(4, weight=1)

        chk = ttk.Frame(opt, style="Card.TFrame")
        chk.pack(fill="x", pady=(8, 0))
        self.keep_audio_var = tk.BooleanVar()
        self.no_auto_sub_var = tk.BooleanVar()
        self.auto_open_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(chk, text="保留下载的音频", variable=self.keep_audio_var).pack(side="left")
        ttk.Checkbutton(chk, text="不使用平台自动字幕", variable=self.no_auto_sub_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(chk, text="完成后自动打开文档", variable=self.auto_open_var).pack(side="left", padx=(16, 0))

        orow = ttk.Frame(opt, style="Card.TFrame")
        orow.pack(fill="x", pady=(10, 0))
        ttk.Label(orow, text="输出目录", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.out_var = tk.StringVar()
        ttk.Entry(orow, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        b_pick = ttk.Button(orow, text="更改…", width=8, command=self._pick_output)
        b_pick.pack(side="left", padx=(6, 0))
        b_open = ttk.Button(orow, text="打开", width=6,
                            command=lambda: paths.open_in_explorer(self._output_dir()))
        b_open.pack(side="left", padx=(6, 0))
        self.register_buttons(b_pick, b_open)

        # ── 日志 ──
        self.console = ConsoleWidget(self, title="转写日志", height=12)
        self.console.pack(fill="both", expand=True, pady=(12, 0))
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))

    # ── 占位符 ──
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
        raw = self.url_text.get("1.0", "end")
        out: list[str] = []
        for line in raw.splitlines():
            line = line.strip().strip('"')
            if not line or line.startswith("#"):
                continue
            # 支持用户粘贴带文字的分享链接，自动提取 http 部分
            m = re.search(r"https?://\S+", line)
            out.append(m.group(0) if m else line)
        return out

    def _update_count(self) -> None:
        n = len(self._urls())
        self.count_var.set(f"已识别 {n} 个链接" if n else "")

    # ══════════════════════════════════════════════════════════════════
    # 配置
    # ══════════════════════════════════════════════════════════════════
    def _load_config(self) -> None:
        self.platform_var.set(_label_of(PLATFORMS, self.cfg.get("platform")))
        self.mode_var.set(_label_of(MODES, self.cfg.get("mode")))
        self.lang_var.set(_label_of(LANGUAGES, self.cfg.get("language")))
        self.browser_var.set(_label_of(BROWSERS, self.cfg.get("browser")))
        self.sub_lang_var.set(self.cfg.get("sub_lang") or "")
        self.keep_audio_var.set(bool(self.cfg.get("keep_audio")))
        self.no_auto_sub_var.set(bool(self.cfg.get("no_auto_sub")))
        self.out_var.set(str(self.cfg.output_dir()))
        self.refresh_models()
        last = self.cfg.get("last_urls") or ""
        if last.strip():
            self._clear_placeholder()
            self.url_text.insert("1.0", last.strip())
            self._update_count()

    def save_config(self) -> None:
        self.cfg.update({
            "platform": _value_of(PLATFORMS, self.platform_var.get(), "auto"),
            "mode": _value_of(MODES, self.mode_var.get(), "auto"),
            "language": _value_of(LANGUAGES, self.lang_var.get(), "auto"),
            "browser": _value_of(BROWSERS, self.browser_var.get(), "chrome"),
            "sub_lang": self.sub_lang_var.get().strip(),
            "keep_audio": bool(self.keep_audio_var.get()),
            "no_auto_sub": bool(self.no_auto_sub_var.get()),
            "model": self._model_value(),
            "output_dir": self.out_var.get().strip(),
            "last_urls": "\n".join(self._urls()),
        })

    def refresh_models(self) -> None:
        """根据本地模型状态刷新模型下拉框。"""
        items = ["自动（用本地最优模型）"]
        for st in model_manager.scan_models():
            mark = "已安装" if st.installed else ("不完整" if st.broken else "未下载")
            items.append(f"{st.size}（{mark}）")
        self.cb_model.configure(values=items)
        want = self.cfg.get("model") or "auto"
        target = items[0]
        for it in items[1:]:
            if it.split("（")[0] == want:
                target = it
                break
        self.model_var.set(target)

    def _model_value(self) -> str:
        label = self.model_var.get()
        if not label or label.startswith("自动"):
            return "auto"
        return label.split("（")[0]

    def _output_dir(self) -> Path:
        raw = self.out_var.get().strip()
        return Path(raw) if raw else paths.default_output_dir()

    def _pick_output(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录",
                                    initialdir=str(self._output_dir()), parent=self)
        if d:
            self.out_var.set(d)
            self.cfg.set("output_dir", d)

    def on_show(self) -> None:
        super().on_show()
        self.refresh_models()

    def on_first_show(self) -> None:
        self.console.append("👋 本页是「一键流程」：粘贴链接 → 点「▶ 开始转写」，直接拿文字稿。", "step")
        self.console.append("    · 有字幕的视频走字幕通路，几秒就能出结果；", "muted")
        self.console.append("    · 没字幕的视频会自动用本地 Whisper 转写（需先装依赖 + 下载模型）；", "muted")
        self.console.append("    · 只想保存视频文件 → 点「⬇ 只下载不转写」，或去「视频下载」页；", "muted")
        self.console.append("    · 已有本地文件要转写 → 去「本地转写」页。", "muted")

    # ══════════════════════════════════════════════════════════════════
    # 启动转写
    # ══════════════════════════════════════════════════════════════════
    def _preflight(self) -> bool:
        rep = self.app.report
        if rep is None:
            return True
        yt = rep.get("yt_dlp")
        if yt and yt.status != "ok":
            if messagebox.askyesno(
                "缺少核心依赖",
                "检测到 yt-dlp 尚未安装，无法下载视频/字幕。\n\n现在去「环境安装」页一键安装吗？",
                parent=self,
            ):
                self.app.show_page("env")
            return False
        ff = rep.get("ffmpeg")
        if ff and ff.status != "ok":
            if not messagebox.askyesno(
                "缺少 FFmpeg",
                "未检测到 FFmpeg，音频下载/转码大概率失败。\n\n仍要继续吗？"
                "（点「否」可到「环境安装」页一键安装）",
                parent=self,
            ):
                self.app.show_page("env")
                return False
        return True

    def _send_to_download(self) -> None:
        """把当前链接交给「视频下载」页（只下载、不转写）。"""
        urls = self._urls()
        if not urls:
            messagebox.showinfo("请输入链接", "请先粘贴至少一个视频链接。", parent=self)
            return
        page = self.app.pages.get("download")
        if page is None:
            return
        page.set_urls(urls)     # type: ignore[attr-defined]
        self.app.show_page("download")

    def _start(self) -> None:
        urls = self._urls()
        if not urls:
            messagebox.showinfo("请输入链接", "请先粘贴至少一个视频链接。", parent=self)
            return
        py = self.require_python()
        if not py:
            return
        if not paths.project_ready():
            messagebox.showerror(
                "缺少程序文件",
                f"在下面的目录里找不到 main.py：\n{paths.runtime_dir()}\n\n"
                "请把 exe 放回 video-summarizer 目录后再运行。",
                parent=self,
            )
            return
        if not self._preflight():
            return

        self.save_config()
        self._output_dir().mkdir(parents=True, exist_ok=True)
        task = jobs.oneshot_task(
            py, urls, self._output_dir(),
            platform=_value_of(PLATFORMS, self.platform_var.get(), "auto"),
            mode=_value_of(MODES, self.mode_var.get(), "auto"),
            model=self._model_value(),
            language=_value_of(LANGUAGES, self.lang_var.get(), "auto"),
            browser=_value_of(BROWSERS, self.browser_var.get(), "chrome"),
            sub_lang=self.sub_lang_var.get().strip(),
            keep_audio=bool(self.keep_audio_var.get()),
            no_auto_sub=bool(self.no_auto_sub_var.get()),
        )
        self.console.append(f"🎬 共 {len(urls)} 个链接，输出目录：{self._output_dir()}", "step")
        self.start_task(task, on_done=lambda code: self._on_finished(task, code))

    # ══════════════════════════════════════════════════════════════════
    # 结果解析
    # ══════════════════════════════════════════════════════════════════
    def _on_finished(self, task: ProcessTask, code: int) -> None:
        data = jobs.parse_result(task.output)
        self.app.refresh_outputs()
        if not data:
            if code not in (0, 130):
                self.console.append("⚠️ 未获取到结构化结果，请查看上方日志定位原因。", "warn")
            return

        ok_list = data.get("success") or []
        err_list = data.get("errors") or []

        if ok_list:
            self.console.append(f"✅ 成功 {len(ok_list)} 个：", "success")
            for r in ok_list:
                src = r.get("source_type")
                src_text = "平台字幕" if src == "subtitle" else f"Whisper({r.get('source_info', {}).get('model', '')})"
                self.console.append(
                    f"    · {r.get('title')}  [{src_text}]  {r.get('char_count', 0)} 字\n"
                    f"      → {r.get('md_path')}", "success")
            if self.auto_open_var.get():
                paths.open_file(ok_list[0].get("md_path", ""))

        for e in err_list:
            etype = e.get("error_type")
            if etype == "whisper_required":
                self._handle_whisper_required(e)
            elif etype == "login_required":
                self._handle_login_required(e)
            else:
                self.console.append(f"❌ {e.get('url')}\n    {e.get('error')}", "error")

        if ok_list and not err_list:
            self.console.append("🎉 全部完成！可到「输出管理」页查看/复制文字稿，"
                                "或把文档丢给 AI 让它总结。", "step")

    def _handle_whisper_required(self, err: dict) -> None:
        self.console.append("⚠️ 该视频没有可用字幕，需要本地 Whisper 转写，但环境未就绪：", "warn")
        for m in err.get("missing", []):
            self.console.append(f"    - {m}", "warn")
        suggested = (err.get("install_guide") or {}).get("suggested_model_size", "medium")
        if messagebox.askyesno(
            "需要本地转写能力",
            "这个视频没有字幕，必须用本地 Whisper 转写，但环境还缺东西：\n\n"
            + "\n".join(f"· {m}" for m in err.get("missing", []))
            + f"\n\n是否现在去准备？（推荐模型：{suggested}）",
            parent=self,
        ):
            rep = self.app.report
            need_dep = True
            if rep:
                item = rep.get("faster_whisper")
                need_dep = not (item and item.status == "ok")
            self.app.show_page("env" if need_dep else "models")

    def _handle_login_required(self, err: dict) -> None:
        platform = err.get("platform", "")
        self.console.append(f"⚠️ {platform} 需要登录态：{err.get('reason')}", "warn")
        guide = err.get("install_guide") or {}
        for key in ("preferred_path_note", "cookies_lifetime_hint"):
            if guide.get(key):
                self.console.append(f"    💡 {guide[key]}", "info")
        if platform in ("bilibili", "douyin") and messagebox.askyesno(
            "需要登录",
            f"{platform} 需要登录后才能下载该视频。\n\n"
            "是否前往「环境安装」页扫码登录？（登录一次可用约 15~30 天）",
            parent=self,
        ):
            self.app.show_page("env")
