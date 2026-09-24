"""
gui/tabs/env_tab.py —— ① 环境安装页

界面能力：
    * 选择 / 扫描 Python 解释器；一键下载便携版 Python（无需用户自己装 Python）
    * 创建独立虚拟环境，避免污染系统环境
    * 环境体检表（Python / pip / yt-dlp / rich / faster-whisper / huggingface_hub /
      playwright / f2 / FFmpeg / Deno / GPU / 模型）
    * 一键安装：基础依赖、Whisper 依赖、登录依赖、抖音增强、FFmpeg、Deno
    * B 站 / 抖音 扫码登录（生成 cookies）
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import env_manager, paths
from ..core.runner import FuncTask, TaskChain
from ..widgets.console import ConsoleWidget
from ..widgets.scrollable import VScrollFrame
from ..widgets.theme import COLORS, card, card_title
from .base import BaseTab

PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple",
    "",
]


class EnvTab(BaseTab):
    title = "环境安装"
    subtitle = "第一次使用请在这里把运行环境装好（Python 依赖 + FFmpeg + 可选 GPU 转写）"

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, app)
        self.report: env_manager.EnvReport | None = None
        self._build()

    # ══════════════════════════════════════════════════════════════════
    # 界面
    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        # 底部：控制台 + 进度条
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="both", expand=False, pady=(10, 0))
        self.console = ConsoleWidget(bottom, title="安装日志", height=9)
        self.console.pack(fill="both", expand=True)
        bar = ttk.Frame(bottom)
        bar.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", length=200)
        self.progress.pack(side="left", fill="x", expand=True)
        self.btn_stop = ttk.Button(bar, text="■ 停止当前任务", style="Danger.TButton",
                                   command=self.stop_task)
        self.btn_stop.pack(side="right", padx=(10, 0))

        scroller = VScrollFrame(self)
        scroller.pack(fill="both", expand=True)
        body = scroller.body

        self._first_card = None
        self._build_stray_card(body)
        self._first_card = self._build_python_card(body)
        self._build_check_card(body)
        self._build_install_card(body)
        self._build_login_card(body)
        self._build_location_card(body)

    # ── ⓪ 残留清理提示（仅在检测到旧版残留时显示）─────────────────────────
    def _build_stray_card(self, parent: tk.Misc) -> None:
        self.stray_card = card(parent)
        self.stray_text = tk.StringVar(value="")
        ttk.Label(self.stray_card, text="🧹 发现程序自动生成的残留文件",
                  style="Warn.TLabel").pack(anchor="w")
        ttk.Label(self.stray_card, textvariable=self.stray_text, style="Muted.TLabel",
                  wraplength=960, justify="left").pack(anchor="w", pady=(4, 8))
        row = ttk.Frame(self.stray_card, style="Card.TFrame")
        row.pack(fill="x")
        self.btn_clean_stray = ttk.Button(row, text="🧹 一键清理这些文件",
                                          style="Accent.TButton",
                                          command=self._clean_stray)
        self.btn_clean_stray.pack(side="left")
        ttk.Button(row, text="📂 查看位置",
                   command=lambda: paths.open_in_explorer(paths.exe_dir())).pack(
            side="left", padx=(8, 0))
        ttk.Button(row, text="以后再说",
                   command=lambda: self.stray_card.pack_forget()).pack(side="left", padx=(8, 0))
        # 默认隐藏，refresh_stray() 里按需显示
        self.stray_card.pack_forget()

    # ── ① Python 解释器 ────────────────────────────────────────────────
    def _build_python_card(self, parent: tk.Misc) -> ttk.Frame:
        c = card(parent)
        c.pack(fill="x", pady=(0, 12))
        card_title(c, "① Python 运行时",
                   "程序本身不需要 Python 也能启动，但下载/转写要靠 Python。没装过 Python 就点右边的「下载便携版」。"
                   ).pack(fill="x", anchor="w")

        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 6))
        ttk.Label(row, text="解释器", style="Card.TLabel", width=8).pack(side="left")
        self.py_var = tk.StringVar(value=self.app.python_path())
        self.py_combo = ttk.Combobox(row, textvariable=self.py_var, state="normal")
        self.py_combo.pack(side="left", fill="x", expand=True)
        self.py_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_python())
        b1 = ttk.Button(row, text="浏览…", width=8, command=self._browse_python)
        b1.pack(side="left", padx=(6, 0))
        b2 = ttk.Button(row, text="扫描", width=6, command=self._scan_pythons)
        b2.pack(side="left", padx=(6, 0))
        b3 = ttk.Button(row, text="应用", width=6, command=self._apply_python)
        b3.pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(c, style="Card.TFrame")
        row2.pack(fill="x", pady=(4, 0))
        b4 = ttk.Button(row2, text="⬇ 下载便携版 Python（免安装）",
                        command=self._setup_embedded)
        b4.pack(side="left")
        b5 = ttk.Button(row2, text="🧪 创建独立虚拟环境 (.venv)", command=self._create_venv)
        b5.pack(side="left", padx=(8, 0))
        b6 = ttk.Button(row2, text="📂 打开程序目录",
                        command=lambda: paths.open_in_explorer(paths.runtime_dir()))
        b6.pack(side="left", padx=(8, 0))

        row3 = ttk.Frame(c, style="Card.TFrame")
        row3.pack(fill="x", pady=(10, 0))
        ttk.Label(row3, text="pip 镜像", style="Card.TLabel", width=8).pack(side="left")
        self.pip_var = tk.StringVar(value=self.cfg.get("pip_index"))
        pip_combo = ttk.Combobox(row3, textvariable=self.pip_var, values=PIP_MIRRORS, width=48)
        pip_combo.pack(side="left")
        ttk.Label(row3, text="（国内建议保留清华源；留空则用官方源）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        self.pip_var.trace_add("write", lambda *_: self.cfg.set("pip_index", self.pip_var.get().strip()))

        self.register_buttons(b1, b2, b3, b4, b5, b6)
        return c

    # ── ② 环境体检 ─────────────────────────────────────────────────────
    def _build_check_card(self, parent: tk.Misc) -> None:
        c = card(parent)
        c.pack(fill="x", pady=(0, 12))
        head = ttk.Frame(c, style="Card.TFrame")
        head.pack(fill="x")
        card_title(head, "② 环境体检",
                   "👉 表格里哪一项没装好，直接双击那一行就会自动安装（也可以选中后点「🔧 安装选中项」）。"
                   ).pack(side="left", anchor="w")
        b = ttk.Button(head, text="🔍 立即检测", style="Accent.TButton", command=self.run_inspect)
        b.pack(side="right")
        self.btn_fix = ttk.Button(head, text="🔧 安装选中项", command=self._fix_selected)
        self.btn_fix.pack(side="right", padx=(0, 8))
        self.register_buttons(b, self.btn_fix)

        cols = ("name", "status", "detail")
        self.tree = ttk.Treeview(c, columns=cols, show="headings", height=12, selectmode="browse")
        self.tree.heading("name", text="组件")
        self.tree.heading("status", text="状态")
        self.tree.heading("detail", text="详情 / 处理建议（双击可直接安装）")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("status", width=110, anchor="w")
        self.tree.column("detail", width=560, anchor="w")
        self.tree.pack(fill="x", pady=(10, 0))
        self.tree.tag_configure("ok", foreground=COLORS["success"])
        self.tree.tag_configure("missing", foreground=COLORS["error"])
        self.tree.tag_configure("optional", foreground=COLORS["muted"])
        self.tree.tag_configure("unknown", foreground=COLORS["warn"])
        # 双击 / 回车 / 右键 都能触发修复，避免用户点了没反应
        self.tree.bind("<Double-1>", self._on_row_activate)
        self.tree.bind("<Return>", self._on_row_activate)
        self.tree.bind("<Button-3>", self._on_row_menu)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        self.summary_var = tk.StringVar(value="尚未检测")
        ttk.Label(c, textvariable=self.summary_var, style="Muted.TLabel",
                  wraplength=980, justify="left").pack(anchor="w", pady=(8, 0))

    # ── ③ 安装组件 ─────────────────────────────────────────────────────
    def _build_install_card(self, parent: tk.Misc) -> None:
        c = card(parent)
        c.pack(fill="x", pady=(0, 12))
        card_title(c, "③ 安装组件",
                   "最少只需「一键安装」这一步：基础依赖 + FFmpeg + Whisper 依赖。装完到「模型管理」下载一个模型即可。"
                   ).pack(fill="x", anchor="w")

        top = ttk.Frame(c, style="Card.TFrame")
        top.pack(fill="x", pady=(10, 8))
        big = ttk.Button(top, text="🚀 一键安装运行环境（推荐）", style="Accent.TButton",
                         command=self._install_all)
        big.pack(side="left")
        self.whisper_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="包含本地 Whisper 转写能力（没有字幕的视频必需）",
                        variable=self.whisper_var).pack(side="left", padx=(12, 0))

        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(fill="x")
        items = [
            ("📦 安装基础依赖", "安装 yt-dlp / rich，字幕与下载通路必需",
             lambda: self._pip_group("base")),
            ("🎙 安装 Whisper 依赖", "安装 faster-whisper / huggingface_hub",
             lambda: self._pip_group("whisper")),
            ("🎬 安装 FFmpeg", "自动下载便携版 FFmpeg（约 90MB）",
             self._install_ffmpeg),
            ("🦕 安装 Deno", "YouTube 部分视频解密需要（约 40MB）",
             self._install_deno),
            ("⬆ 升级 yt-dlp", "平台风控变化后最常用的修复手段",
             self._upgrade_ytdlp),
            ("🔐 安装登录依赖", "playwright + Chromium，用于 B 站/抖音扫码登录",
             self._install_login),
            ("🎵 安装抖音增强", "安装 f2，绕过抖音部分风控",
             lambda: self._pip_group("douyin")),
            ("🧹 清理下载缓存", "删除 downloads 目录下的临时音频/字幕",
             self._clean_downloads),
        ]
        for idx, (label, hint, cmd) in enumerate(items):
            r, col = divmod(idx, 2)
            cell = ttk.Frame(grid, style="Card.TFrame")
            cell.grid(row=r, column=col, sticky="ew", padx=(0, 14), pady=5)
            btn = ttk.Button(cell, text=label, width=22, command=cmd)
            btn.pack(side="left")
            ttk.Label(cell, text=hint, style="Muted.TLabel").pack(side="left", padx=(10, 0))
            self.register_buttons(btn)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self.register_buttons(big)

    # ── ④ 平台登录 ─────────────────────────────────────────────────────
    def _build_login_card(self, parent: tk.Misc) -> None:
        c = card(parent)
        c.pack(fill="x", pady=(0, 12))
        card_title(c, "④ 平台登录（可选）",
                   "B 站高清/会员视频、抖音视频常需要登录态。点击后会弹出浏览器窗口，手机扫码即可，"
                   "cookies 会自动保存到程序目录。").pack(fill="x", anchor="w")

        # 浏览器选择：默认复用本机已装的 Edge/Chrome，不必额外下载 Chromium
        brow = ttk.Frame(c, style="Card.TFrame")
        brow.pack(fill="x", pady=(10, 0))
        ttk.Label(brow, text="登录浏览器", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.login_browser_var = tk.StringVar()
        self.cb_login_browser = ttk.Combobox(brow, textvariable=self.login_browser_var,
                                             state="readonly", width=40)
        self.cb_login_browser.pack(side="left")
        b0 = ttk.Button(brow, text="🔄 重新扫描", width=11, command=self.refresh_browsers)
        b0.pack(side="left", padx=(6, 0))
        self.browser_hint_var = tk.StringVar(value="")
        ttk.Label(brow, textvariable=self.browser_hint_var, style="Muted.TLabel").pack(
            side="left", padx=(10, 0))

        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        b1 = ttk.Button(row, text="📱 B 站扫码登录", command=lambda: self._login("bilibili"))
        b1.pack(side="left")
        b2 = ttk.Button(row, text="📱 抖音扫码登录", command=lambda: self._login("douyin"))
        b2.pack(side="left", padx=(8, 0))
        b3 = ttk.Button(row, text="📂 打开 cookies 目录",
                        command=lambda: paths.open_in_explorer(paths.runtime_dir()))
        b3.pack(side="left", padx=(8, 0))
        b4 = ttk.Button(row, text="⬇ 下载 Chromium（可选）", command=self._install_chromium)
        b4.pack(side="left", padx=(8, 0))
        ttk.Label(c, text="💡 默认会复用你电脑上已安装的 Edge / Chrome，不需要额外下载浏览器。"
                          "只有本机完全没有 Chromium 系浏览器时，才需要点「⬇ 下载 Chromium」。\n"
                          "也可以用浏览器插件 J2TEAM Cookies 导出 JSON，覆盖 "
                          "youtube_cookies.json / bilibili_cookies.json / douyin_cookies.json。",
                  style="Muted.TLabel", wraplength=960, justify="left").pack(anchor="w", pady=(8, 0))
        self.register_buttons(b0, b1, b2, b3, b4)

    # ── ⑤ 文件位置 ─────────────────────────────────────────────────────
    def _build_location_card(self, parent: tk.Misc) -> None:
        c = card(parent)
        c.pack(fill="x", pady=(0, 12))
        if paths.is_standalone():
            desc = (f"绿色免安装模式：所有内容都集中在 exe 旁边的 "
                    f"「{paths.WORK_DIRNAME}」文件夹里。\n"
                    "整个文件夹拷走 = 搬家（模型、cookies、文字稿都跟着走）；"
                    "删掉它 = 干净卸载。")
        else:
            desc = "项目目录内运行：脚本、模型、输出都在项目目录里，与命令行用法共用一套文件。"
        card_title(c, "⑤ 文件位置", desc).pack(fill="x", anchor="w")

        rows = [
            ("工作目录", paths.runtime_dir(), "脚本 + 数据都在这里"),
            ("文字稿输出", paths.default_output_dir(), "转写结果 *.transcript.md"),
            ("下载的视频", paths.videos_dir(), "「视频下载」页保存的文件"),
            ("Whisper 模型", paths.models_dir(), "体积较大，可在模型管理页删除"),
            ("运行日志", paths.logs_dir(), "出问题时先看 gui.log"),
        ]
        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))
        for i, (label, path, hint) in enumerate(rows):
            ttk.Label(grid, text=label, style="Card.TLabel", width=12).grid(
                row=i, column=0, sticky="w", pady=3)
            ttk.Label(grid, text=str(path), style="Muted.TLabel").grid(
                row=i, column=1, sticky="w", padx=(6, 10), pady=3)
            ttk.Button(grid, text="打开", width=6,
                       command=lambda p=path: paths.open_in_explorer(p)).grid(
                row=i, column=2, sticky="w", pady=3)
            ttk.Label(grid, text=hint, style="Muted.TLabel").grid(
                row=i, column=3, sticky="w", padx=(10, 0), pady=3)
        grid.columnconfigure(1, weight=1)

        if paths.data_dir_is_fallback():
            ttk.Label(c, text=f"⚠️ exe 所在位置不可写，数据已退回系统目录：{paths.data_dir()}\n"
                              "    把 exe 换到普通文件夹（如桌面、D 盘）即可恢复绿色模式。",
                      style="Warn.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        # 旧版本曾把数据放进 %LOCALAPPDATA%，检测到就提供清理
        cnt, size = paths.legacy_appdata_summary()
        if cnt > 0:
            box = ttk.Frame(c, style="Card.TFrame")
            box.pack(fill="x", pady=(10, 0))
            ttk.Label(box, text=f"🧹 发现旧版本遗留的系统目录（{cnt} 项 / "
                                f"{paths.fmt_bytes(size)}）：{paths.legacy_app_home()}",
                      style="Muted.TLabel", wraplength=900, justify="left").pack(anchor="w")
            row2 = ttk.Frame(box, style="Card.TFrame")
            row2.pack(fill="x", pady=(6, 0))
            ttk.Button(row2, text="📂 打开看看",
                       command=lambda: paths.open_in_explorer(paths.legacy_app_home())).pack(side="left")
            ttk.Button(row2, text="🗑 删除旧目录", style="Danger.TButton",
                       command=self._clean_legacy).pack(side="left", padx=(8, 0))

    # ══════════════════════════════════════════════════════════════════
    # 行为
    # ══════════════════════════════════════════════════════════════════
    def on_first_show(self) -> None:
        self._scan_pythons(silent=True)
        self.refresh_browsers(silent=True)
        self.console.append("👋 欢迎使用视频转写助手。第一次用请点右上角「🔍 立即检测」查看环境情况。", "step")
        self.console.append(f"    工作目录：{paths.runtime_dir()}", "muted")
        if paths.is_standalone():
            self.console.append(f"    ℹ️ 绿色模式：所有文件都在 exe 旁边的 "
                                f"「{paths.WORK_DIRNAME}」文件夹里，整体拷走即可搬家。", "muted")
        self.refresh_stray()
        self.run_inspect()

    # ── 残留清理 ───────────────────────────────────────────────────────
    def refresh_stray(self) -> None:
        """检测 exe 旁边是否有旧版留下的残留文件，有则显示清理卡片。"""
        try:
            count, size = paths.stray_payload_summary()
        except Exception:
            count, size = 0, 0
        if count <= 0:
            self.stray_card.pack_forget()
            return
        self.stray_text.set(
            f"在 exe 所在目录发现 {count} 个由早期版本**散落**生成的文件/文件夹"
            f"（约 {paths.fmt_bytes(size)}）：\n{paths.exe_dir()}\n\n"
            f"新版本已把这些内容统一收纳进「{paths.WORK_DIRNAME}」文件夹，"
            "所以这些散落的旧文件可以安全清理"
            "（output / videos / models / .venv 等可能含你产物的目录会保留）。"
        )
        # 固定显示在页面最顶部
        if self._first_card is not None:
            self.stray_card.pack(fill="x", pady=(0, 12), before=self._first_card)
        else:
            self.stray_card.pack(fill="x", pady=(0, 12))
        self.console.append(f"🧹 检测到 exe 旁边有 {count} 个残留文件，可在页面顶部一键清理。", "warn")

    def _clean_stray(self) -> None:
        items = paths.stray_payload_files()
        if not items:
            self.stray_card.pack_forget()
            return
        names = "、".join(p.name for p in items[:12])
        more = f" 等 {len(items)} 项" if len(items) > 12 else ""
        if not messagebox.askyesno(
            "确认清理",
            f"将从以下目录删除程序自动生成的文件：\n{paths.exe_dir()}\n\n"
            f"{names}{more}\n\n"
            "（output / videos / models / .venv 会保留；你自己的文件不会被触碰）\n\n确定吗？",
            parent=self,
        ):
            return
        removed, freed, errors = paths.cleanup_stray_payload()
        self.console.append(f"🧹 已清理 {removed} 项，释放 {paths.fmt_bytes(freed)}",
                            "success" if removed else "warn")
        for e in errors:
            self.console.append(f"    ⚠️ 删除失败 {e}", "warn")
        self.refresh_stray()

    def _clean_legacy(self) -> None:
        """删除旧版本遗留在 %LOCALAPPDATA% 的数据目录。"""
        d = paths.legacy_app_home()
        cnt, size = paths.legacy_appdata_summary()
        if cnt <= 0:
            self.console.append("ℹ️ 没有需要清理的旧目录。", "muted")
            return
        if not messagebox.askyesno(
            "删除旧数据目录",
            f"将整个删除：\n{d}\n\n共 {cnt} 项，约 {paths.fmt_bytes(size)}。\n\n"
            "⚠️ 如果里面有你要留的文字稿或已下载的模型，请先手动拷出来。\n\n确定删除吗？",
            parent=self,
        ):
            return
        ok, msg = paths.cleanup_legacy_appdata()
        self.console.append(("✅ " if ok else "❌ ") + msg, "success" if ok else "error")

    # ── 登录浏览器 ─────────────────────────────────────────────────────
    def refresh_browsers(self, silent: bool = False) -> None:
        """扫描本机浏览器，填充「登录浏览器」下拉框。"""
        found = env_manager.list_local_browsers()
        self._browser_map: dict[str, str] = {}
        labels: list[str] = []

        if found:
            first = f"自动（优先用 {found[0][1]}）"
            labels.append(first)
            self._browser_map[first] = "auto"
        else:
            labels.append("自动")
            self._browser_map["自动"] = "auto"

        for key, name, path in found:
            label = f"{name}（本机已安装）"
            labels.append(label)
            self._browser_map[label] = key

        pw_ok = env_manager.playwright_chromium_installed(self.python_path())
        pw_label = "Chromium（Playwright 自带）" + ("" if pw_ok else " — 未下载")
        labels.append(pw_label)
        self._browser_map[pw_label] = "playwright"

        self.cb_login_browser.configure(values=labels)
        saved = self.cfg.get("login_browser") or "auto"
        target = labels[0]
        for lb in labels:
            if self._browser_map.get(lb) == saved:
                target = lb
                break
        self.login_browser_var.set(target)

        if found:
            self.browser_hint_var.set(f"✓ 已发现 {len(found)} 个本机浏览器，无需下载 Chromium")
        elif pw_ok:
            self.browser_hint_var.set("将使用 Playwright 自带 Chromium")
        else:
            self.browser_hint_var.set("⚠ 未发现浏览器：装个 Edge/Chrome，或点「⬇ 下载 Chromium」")

        if not silent:
            self.console.append("🌐 本机可用于扫码登录的浏览器：", "info")
            if found:
                for key, name, path in found:
                    self.console.append(f"    - {name}  [{path}]", "muted")
            else:
                self.console.append("    （未检测到 Edge / Chrome / Brave / Chromium）", "warn")
            self.console.append(
                f"    Playwright 自带 Chromium：{'已下载' if pw_ok else '未下载'}", "muted")

    def _login_browser(self) -> str:
        label = self.login_browser_var.get()
        value = getattr(self, "_browser_map", {}).get(label, "auto")
        self.cfg.set("login_browser", value)
        return value

    def _install_chromium(self) -> None:
        py = self.require_python()
        if not py:
            return
        found = env_manager.list_local_browsers()
        if found and not messagebox.askyesno(
            "其实不需要下载",
            f"检测到本机已安装：{'、'.join(b[1] for b in found)}\n\n"
            "扫码登录可以直接复用它们，无需再下载 Chromium（约 150MB）。\n\n"
            "仍要下载吗？",
            parent=self,
        ):
            return
        self.start_task(env_manager.playwright_browser_task(py),
                        on_done=lambda code: (self.refresh_browsers(silent=True),
                                              self.run_inspect()))

    # ── Python 选择 ────────────────────────────────────────────────────
    def _scan_pythons(self, silent: bool = False) -> None:
        cands = env_manager.find_python_candidates()
        self.py_combo.configure(values=cands)
        cur = self.app.python_path()
        if cur:
            self.py_var.set(cur)
        elif cands:
            self.py_var.set(cands[0])
        if not silent:
            self.console.append(f"🔎 共发现 {len(cands)} 个 Python 解释器：", "info")
            for c in cands:
                self.console.append(f"    - {c}", "muted")
            if not cands:
                self.console.append("    未发现任何 Python，请点击「⬇ 下载便携版 Python（免安装）」", "warn")

    def _browse_python(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 python.exe",
            filetypes=[("Python 解释器", "python.exe"), ("全部文件", "*.*")],
            parent=self,
        )
        if path:
            self.py_var.set(path)
            self._apply_python()

    def _apply_python(self) -> None:
        path = self.py_var.get().strip().strip('"')
        if path and not Path(path).is_file():
            messagebox.showerror("路径无效", f"找不到文件：\n{path}", parent=self)
            return
        self.cfg.set("python_path", path)
        self.console.append(f"✅ 已设置解释器：{path or '(自动)'}", "success")
        self.app.notify_env_changed()
        self.run_inspect()

    # ── 体检 ──────────────────────────────────────────────────────────
    def run_inspect(self) -> None:
        if self.busy:
            return
        holder: dict = {}

        def _work(task: FuncTask) -> int:
            task.emit("🔍 正在体检运行环境（依赖 / FFmpeg / GPU / 模型）...", "info")
            holder["report"] = env_manager.inspect_environment(self.cfg)
            return 0

        def _done(code: int) -> None:
            rep = holder.get("report")
            if rep:
                self._render_report(rep)

        self.start_task(FuncTask(_work, title="环境体检"), on_done=_done, banner=False)

    def _render_report(self, rep: env_manager.EnvReport) -> None:
        self.report = rep
        self.app.set_report(rep)
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        actions = self._fix_actions()
        for item in rep.items:
            if item.status == "ok":
                action = item.detail
            elif item.key in actions:
                # 直接告诉用户「双击这一行」，而不是让他去别处找按钮
                action = f"{item.detail}   → {actions[item.key][2]}"
            else:
                action = item.detail if not item.hint else f"{item.detail}   → {item.hint}"
            self.tree.insert("", "end", iid=item.key, tags=(item.status,), values=(
                f"{item.icon} {item.name}", item.status_text, action,
            ))
        missing = [i.name for i in rep.items if i.status == "missing"]
        if not missing:
            self.summary_var.set("🎉 环境完备，可以直接到「一键转写」页开始使用。")
            self.console.append("🎉 环境完备！", "success")
        else:
            self.summary_var.set("⚠️ 待处理：" + "、".join(missing) +
                                 "     （双击表格中带 ✗ 的行即可单独安装；"
                                 "或点下方「🚀 一键安装运行环境」一次全部解决）")
            self.console.append("⚠️ 缺少组件：" + "、".join(missing), "warn")
            self.console.append("    💡 直接双击表格里对应那一行，就会开始安装。", "step")
        # 自动选中第一个缺失项，方便直接点「🔧 安装选中项」
        for item in rep.items:
            if item.status == "missing" and self.tree.exists(item.key):
                self.tree.selection_set(item.key)
                self.tree.focus(item.key)
                break

    # ══════════════════════════════════════════════════════════════════
    # 体检表 → 一键修复
    # ══════════════════════════════════════════════════════════════════
    def _fix_actions(self) -> dict:
        """组件 key → (动作说明, 执行函数, 表格里的提示文案)。

        没列进来的项表示无法自动处理，表格里会沿用 CheckItem.hint。
        """
        base = ("安装基础依赖 (yt-dlp / rich)", lambda: self._pip_group("base"),
                "双击本行立即安装")
        whisper = ("安装 Whisper 依赖 (faster-whisper)", lambda: self._pip_group("whisper"),
                   "双击本行立即安装")
        return {
            "pip": ("初始化 pip", self._ensure_pip, "双击本行立即修复"),
            "yt_dlp": base,
            "rich": base,
            "faster_whisper": whisper,
            "huggingface_hub": whisper,
            "playwright": ("安装扫码登录依赖 (playwright 库)", self._install_login,
                           "双击本行立即安装（约 5MB，浏览器复用本机）"),
            "browser": ("下载 Playwright Chromium", self._install_chromium,
                        "双击本行下载 Chromium（本机已有 Edge/Chrome 则无需下载）"),
            "f2": ("安装抖音增强 (f2)", lambda: self._pip_group("douyin"),
                   "双击本行立即安装"),
            "ffmpeg": ("下载便携版 FFmpeg", self._install_ffmpeg,
                       "双击本行立即下载（约 90MB）"),
            "deno": ("下载 Deno", self._install_deno, "双击本行立即下载（约 40MB）"),
            "python": ("下载便携版 Python", self._setup_embedded,
                       "双击本行下载便携版 Python"),
            "model": ("前往「模型管理」下载 Whisper 模型", self._goto_models,
                      "双击本行前往「模型管理」下载"),
            "gpu": ("安装 Whisper 依赖（GPU 检测依赖 ctranslate2）",
                    lambda: self._pip_group("whisper"),
                    "双击本行安装 Whisper 依赖后可再检测"),
        }

    def _on_row_activate(self, _event=None) -> None:
        self._fix_selected()

    def _on_row_select(self, _event=None) -> None:
        """选中行后，把按钮文字改成该行将要执行的动作，让用户知道点了会干什么。"""
        sel = self.tree.selection()
        actions = self._fix_actions()
        if sel and sel[0] in actions:
            item = self.report.get(sel[0]) if self.report else None
            if item and item.status == "ok":
                self.btn_fix.configure(text="🔧 安装选中项")
            else:
                label = actions[sel[0]][0]
                self.btn_fix.configure(text=f"🔧 {label[:18]}")
        else:
            self.btn_fix.configure(text="🔧 安装选中项")

    def _on_row_menu(self, event) -> None:
        """右键：选中该行并弹出操作菜单。"""
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        actions = self._fix_actions()
        menu = tk.Menu(self, tearoff=0)
        if iid in actions:
            menu.add_command(label=f"🔧 {actions[iid][0]}", command=self._fix_selected)
            menu.add_separator()
        menu.add_command(label="🔍 重新检测", command=self.run_inspect)
        menu.add_command(label="🚀 一键安装运行环境", command=self._install_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _fix_selected(self) -> None:
        """安装表格中选中的那一项。"""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "请先选择一项",
                "请在上面的体检表里点一下要安装的组件（带 ✗ 的行），再点「🔧 安装选中项」。\n\n"
                "小技巧：直接双击那一行就会开始安装。",
                parent=self,
            )
            return
        key = sel[0]
        item = self.report.get(key) if self.report else None

        if item and item.status == "ok":
            if key == "model":
                self._goto_models()
                return
            messagebox.showinfo("无需安装", f"「{item.name}」已经就绪了。", parent=self)
            return

        actions = self._fix_actions()
        if key not in actions:
            messagebox.showinfo(
                "该项无法自动安装",
                f"「{item.name if item else key}」不能自动处理。\n\n"
                + (item.hint or "请参考「详情 / 处理建议」列的说明。" if item else ""),
                parent=self,
            )
            return

        label, func, _hint = actions[key]
        name = item.name if item else key
        self.console.append(f"🔧 开始处理「{name}」：{label}", "step")
        func()

    def _ensure_pip(self) -> None:
        py = self.require_python()
        if not py:
            return
        self.start_task(env_manager.ensure_pip_task(py, self.cfg),
                        on_done=lambda code: self.run_inspect())

    def _goto_models(self) -> None:
        self.console.append("📦 请在「模型管理」页选择模型（推荐 medium）后点「⬇ 下载选中模型」。", "step")
        self.app.show_page("models")

    # ── 安装动作 ──────────────────────────────────────────────────────
    def _pip_group(self, group: str) -> None:
        py = self.require_python()
        if not py:
            return
        self.start_task(env_manager.pip_install_requirements_task(py, group, self.cfg),
                        on_done=lambda code: self.run_inspect() if code == 0 else None)

    def _install_all(self) -> None:
        py = self.require_python()
        if not py:
            return
        chain = env_manager.install_all_task(self.python_path, self.cfg,
                                             with_whisper=self.whisper_var.get())
        self.start_task(chain, on_done=lambda code: self.run_inspect())

    def _install_ffmpeg(self) -> None:
        self.start_task(env_manager.install_ffmpeg_task(),
                        on_done=lambda code: self.run_inspect())

    def _install_deno(self) -> None:
        self.start_task(env_manager.install_deno_task(),
                        on_done=lambda code: self.run_inspect())

    def _upgrade_ytdlp(self) -> None:
        py = self.require_python()
        if not py:
            return
        self.start_task(env_manager.upgrade_ytdlp_task(py, self.cfg),
                        on_done=lambda code: self.run_inspect())

    def _install_login(self) -> None:
        """安装扫码登录环境：只装 playwright 库；本机没浏览器才补 Chromium。"""
        py = self.require_python()
        if not py:
            return
        found = env_manager.list_local_browsers()
        if found:
            self.console.append(
                f"🌐 检测到本机浏览器（{'、'.join(b[1] for b in found)}），"
                "只安装 playwright 库，跳过 150MB 的 Chromium 下载。", "info")
            self._install_playwright_lib()
            return
        chain = TaskChain([
            lambda: env_manager.pip_install_requirements_task(self.python_path(), "login", self.cfg),
            lambda: env_manager.playwright_browser_task(self.python_path()),
        ], title="安装扫码登录环境（含 Chromium）")
        self.start_task(chain, on_done=lambda code: (self.refresh_browsers(silent=True),
                                                     self.run_inspect()))

    def _setup_embedded(self) -> None:
        if not messagebox.askyesno(
            "下载便携版 Python",
            f"将从网络下载官方便携版 Python {env_manager.EMBED_PY_VERSION}（约 11MB）"
            f"并自动配置 pip。\n\n安装位置：\n{paths.tools_dir() / 'python-embed'}\n\n是否继续？",
            parent=self,
        ):
            return

        def _done(code: int) -> None:
            if code == 0:
                self.py_var.set(str(env_manager.embedded_python()))
                self._scan_pythons(silent=True)
                self.app.notify_env_changed()
            self.run_inspect()

        self.start_task(env_manager.setup_embedded_python_task(self.cfg), on_done=_done)

    def _create_venv(self) -> None:
        base = self.py_var.get().strip() or self.python_path()
        if not base:
            messagebox.showwarning("缺少基础解释器", "请先选择或下载一个 Python 解释器。", parent=self)
            return

        def _done(code: int) -> None:
            if code == 0:
                self.py_var.set(str(env_manager.venv_python()))
                self._scan_pythons(silent=True)
                self.app.notify_env_changed()
            self.run_inspect()

        self.start_task(env_manager.create_venv_task(base, self.cfg), on_done=_done)

    def _login(self, platform: str) -> None:
        py = self.require_python()
        if not py:
            return

        # 1) playwright 库（很小，约 5MB）必须有
        rep = self.report
        if rep is not None:
            item = rep.get("playwright")
            if item and item.status != "ok":
                if messagebox.askyesno(
                    "缺少登录依赖",
                    "扫码登录需要 playwright 库（约 5MB）。\n\n"
                    "浏览器会复用你电脑上已装的 Edge / Chrome，不会额外下载 Chromium。\n\n"
                    "现在安装 playwright 吗？",
                    parent=self,
                ):
                    self._install_playwright_lib()
                return

        # 2) 浏览器：本机有就直接用，没有才提示
        browser = self._login_browser()
        found = env_manager.list_local_browsers()
        if browser == "playwright" and not env_manager.playwright_chromium_installed(py):
            if messagebox.askyesno(
                "Chromium 未下载",
                "你选择了「Playwright 自带 Chromium」，但它还没下载（约 150MB）。\n\n"
                + (f"其实本机已有 {found[0][1]}，建议改选「自动」直接复用。\n\n现在去下载 Chromium 吗？"
                   if found else "现在下载吗？"),
                parent=self,
            ):
                self._install_chromium()
            return
        if browser == "auto" and not found and not env_manager.playwright_chromium_installed(py):
            messagebox.showwarning(
                "没有可用浏览器",
                "本机未检测到 Edge / Chrome / Brave，Playwright 自带 Chromium 也未下载。\n\n"
                "请任选其一：\n"
                "  · 安装 Microsoft Edge 或 Google Chrome（推荐，一般系统自带 Edge）\n"
                "  · 点「⬇ 下载 Chromium（可选）」下载约 150MB",
                parent=self,
            )
            return

        if browser == "auto" and found:
            self.console.append(f"🌐 将复用本机浏览器：{found[0][1]}（无需下载 Chromium）", "info")
        elif browser not in ("auto", "playwright"):
            self.console.append(f"🌐 指定浏览器：{browser}", "info")

        self.console.append("📱 即将弹出浏览器窗口，请用手机扫码并在页面上完成登录；"
                            "登录成功后窗口会自动关闭。", "step")
        self.start_task(env_manager.login_task(py, platform, browser=browser),
                        on_done=lambda code: self.run_inspect() if code == 0 else None)

    def _install_playwright_lib(self) -> None:
        """只装 playwright 库，不下载 Chromium（本机浏览器够用）。"""
        py = self.require_python()
        if not py:
            return
        self.start_task(
            env_manager.pip_install_requirements_task(py, "login", self.cfg),
            on_done=lambda code: (self.refresh_browsers(silent=True), self.run_inspect()),
        )

    def _clean_downloads(self) -> None:
        from ..core import outputs
        n, freed = outputs.clean_downloads(paths.downloads_dir())
        self.console.append(f"🧹 已清理 {n} 个临时文件，释放 {paths.fmt_bytes(freed)}", "success")
