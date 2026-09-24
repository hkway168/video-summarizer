"""
gui/tabs/local_tab.py —— 「本地转写」页

对本地已有的音频/视频文件做 Whisper 转写，完全不联网：
    * 添加文件（多选）/ 添加整个目录 / 从下载页一键带入
    * 列表展示类型、体积、时长，可移除单项或清空
    * 选模型、语言、是否输出时间戳
    * 生成的 transcript.md 与一键流程同构，「输出管理」页可直接查看
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import jobs, media, model_manager, paths
from ..core.runner import ProcessTask
from ..widgets.console import ConsoleWidget
from ..widgets.theme import COLORS, card, card_title
from .base import BaseTab

LANGUAGES = [
    ("自动检测", "auto"), ("中文", "zh"), ("英文", "en"),
    ("日文", "ja"), ("韩文", "ko"), ("法文", "fr"), ("德文", "de"),
]


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


class LocalTab(BaseTab):
    title = "本地转写"
    subtitle = "把电脑里已有的音频/视频转成文字稿：录屏、会议录音、下载好的视频都可以"

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, app)
        self.files: list[media.MediaFile] = []
        self._probing = False
        self._build()
        self._load_config()

    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        head = card(self)
        head.pack(fill="x")
        card_title(head, "待转写文件",
                   "支持 mp4 / mkv / mov / avi / webm 等视频，以及 mp3 / m4a / wav / flac 等音频。"
                   "视频会自动用 FFmpeg 抽出音轨。").pack(fill="x", anchor="w")

        row = ttk.Frame(head, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        b1 = ttk.Button(row, text="➕ 添加文件…", command=self._add_files_dialog)
        b1.pack(side="left")
        b2 = ttk.Button(row, text="📂 添加目录…", command=self._add_dir_dialog)
        b2.pack(side="left", padx=(8, 0))
        b3 = ttk.Button(row, text="⬇ 从下载页带入", command=self._pull_from_download)
        b3.pack(side="left", padx=(8, 0))
        b4 = ttk.Button(row, text="移除选中", command=self._remove_selected)
        b4.pack(side="left", padx=(8, 0))
        b5 = ttk.Button(row, text="清空", command=self._clear_files)
        b5.pack(side="left", padx=(8, 0))
        self.recursive_var = tk.BooleanVar()
        ttk.Checkbutton(row, text="添加目录时含子目录",
                        variable=self.recursive_var).pack(side="left", padx=(16, 0))
        self.register_buttons(b1, b2, b3, b4, b5)

        listbox = card(self, padding=10)
        listbox.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("name", "kind", "size", "duration", "path")
        self.tree = ttk.Treeview(listbox, columns=cols, show="headings",
                                 height=7, selectmode="extended")
        for key, text, width, anchor, stretch in (
            ("name", "文件名", 280, "w", True),
            ("kind", "类型", 60, "w", False),
            ("size", "体积", 90, "e", False),
            ("duration", "时长", 80, "e", False),
            ("path", "所在目录", 320, "w", True),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        sb = ttk.Scrollbar(listbox, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._open_selected())
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        self.stat_var = tk.StringVar(value="尚未添加文件")
        ttk.Label(self, textvariable=self.stat_var, style="MutedBg.TLabel").pack(anchor="w", pady=(6, 0))

        # ── 参数 ──
        opt = card(self)
        opt.pack(fill="x", pady=(10, 0))
        card_title(opt, "转写参数").pack(fill="x", anchor="w")
        grid = ttk.Frame(opt, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))

        ttk.Label(grid, text="模型", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self.model_var = tk.StringVar()
        self.cb_model = ttk.Combobox(grid, textvariable=self.model_var, state="readonly", width=26)
        self.cb_model.grid(row=0, column=1, sticky="ew", pady=5, padx=(0, 18))

        ttk.Label(grid, text="语言", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=5)
        self.lang_var = tk.StringVar()
        ttk.Combobox(grid, textvariable=self.lang_var, state="readonly",
                     values=_labels(LANGUAGES), width=16).grid(row=0, column=3, sticky="w", pady=5)

        self.ts_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(grid, text="输出带时间戳的段落",
                        variable=self.ts_var).grid(row=0, column=4, sticky="w", padx=(18, 0), pady=5)
        grid.columnconfigure(1, weight=1)

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

        act = ttk.Frame(opt, style="Card.TFrame")
        act.pack(fill="x", pady=(10, 0))
        self.btn_start = ttk.Button(act, text="▶ 开始转写", style="Accent.TButton",
                                    command=self._start)
        self.btn_start.pack(side="left")
        ttk.Button(act, text="■ 停止", style="Danger.TButton",
                   command=self.stop_task).pack(side="left", padx=(8, 0))
        self.register_buttons(self.btn_start)

        self.console = ConsoleWidget(self, title="转写日志", height=9)
        self.console.pack(fill="both", expand=True, pady=(12, 0))
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))

    # ══════════════════════════════════════════════════════════════════
    # 文件清单
    # ══════════════════════════════════════════════════════════════════
    def add_files(self, items: list[str] | list[Path], clear: bool = False) -> None:
        """对外接口：添加文件（下载页会调用）。"""
        if clear:
            self.files = []
        expanded = media.expand_inputs(items, recursive=bool(self.recursive_var.get()))
        exist = {str(f.path.resolve()).lower() for f in self.files}
        added = 0
        for p in expanded:
            if str(p.resolve()).lower() in exist:
                continue
            mf = media.make_media_file(p)
            if mf:
                self.files.append(mf)
                exist.add(str(p.resolve()).lower())
                added += 1
        self._render()
        if added:
            self.console.append(f"➕ 已添加 {added} 个文件（共 {len(self.files)} 个）", "info")
            self._probe_durations()
        elif expanded:
            self.console.append("ℹ️ 这些文件已经在列表里了", "muted")

    def _add_files_dialog(self) -> None:
        picked = filedialog.askopenfilenames(
            title="选择要转写的音频/视频文件",
            filetypes=media.FILE_DIALOG_TYPES,
            parent=self,
        )
        if picked:
            self.add_files(list(picked))

    def _add_dir_dialog(self) -> None:
        d = filedialog.askdirectory(title="选择包含音视频的目录", parent=self)
        if not d:
            return
        found = media.scan_dir(d, recursive=bool(self.recursive_var.get()))
        if not found:
            messagebox.showinfo("没有找到媒体文件",
                                f"目录里没有可识别的音视频文件：\n{d}", parent=self)
            return
        self.add_files([str(p) for p in found])

    def _pull_from_download(self) -> None:
        page = self.app.pages.get("download")
        files = [f for f in getattr(page, "last_files", []) if Path(f).is_file()]
        if not files:
            messagebox.showinfo("没有可带入的文件",
                                "「视频下载」页还没有成功下载的文件。", parent=self)
            return
        self.add_files(files)

    def _remove_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idxs = {int(i) for i in sel if i.isdigit()}
        self.files = [f for i, f in enumerate(self.files) if i not in idxs]
        self._render()

    def _clear_files(self) -> None:
        self.files = []
        self._render()

    def _open_selected(self) -> None:
        sel = self.tree.selection()
        if sel and sel[0].isdigit():
            paths.open_file(self.files[int(sel[0])].path)

    def _render(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for idx, f in enumerate(self.files):
            self.tree.insert("", "end", iid=str(idx), values=(
                f.name, f.kind, f.size_text, f.duration_text, str(f.path.parent),
            ))
        if self.files:
            self.stat_var.set(f"共 {len(self.files)} 个文件 · "
                              f"{paths.fmt_bytes(media.total_bytes(self.files))}")
        else:
            self.stat_var.set("尚未添加文件（点「➕ 添加文件…」或「📂 添加目录…」）")
        self.cfg.set("local_files", [str(f.path) for f in self.files])

    def _probe_durations(self) -> None:
        """后台线程用 ffprobe 补齐时长。

        故意不走 start_task：读时长只是锦上添花，不应占用页面的任务槽，
        否则用户紧接着点「开始转写」会被误判为「已有任务在运行」。
        """
        pending = [f for f in self.files if f.duration <= 0]
        if not pending or not media.find_ffprobe() or self._probing:
            return
        self._probing = True

        def _work() -> None:
            try:
                for f in pending[:200]:
                    f.duration = media.probe_duration(f.path, timeout=8)
            finally:
                self._probing = False
                try:
                    self.after(0, self._render)
                except Exception:
                    pass

        threading.Thread(target=_work, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════
    # 配置
    # ══════════════════════════════════════════════════════════════════
    def _load_config(self) -> None:
        self.lang_var.set(_label_of(LANGUAGES, self.cfg.get("local_language")))
        self.ts_var.set(bool(self.cfg.get("local_timestamps")))
        self.recursive_var.set(bool(self.cfg.get("local_recursive")))
        self.out_var.set(str(self.cfg.output_dir()))
        self.refresh_models()
        saved = self.cfg.get("local_files") or []
        if isinstance(saved, list) and saved:
            keep = [s for s in saved if Path(s).is_file()]
            if keep:
                self.add_files(keep)

    def save_config(self) -> None:
        self.cfg.update({
            "local_model": self._model_value(),
            "local_language": _value_of(LANGUAGES, self.lang_var.get(), "auto"),
            "local_timestamps": bool(self.ts_var.get()),
            "local_recursive": bool(self.recursive_var.get()),
            "local_files": [str(f.path) for f in self.files],
        })

    def refresh_models(self) -> None:
        items = ["自动（用本地最优模型）"]
        for st in model_manager.scan_models():
            mark = "已安装" if st.installed else ("不完整" if st.broken else "未下载")
            items.append(f"{st.size}（{mark}）")
        self.cb_model.configure(values=items)
        want = self.cfg.get("local_model") or "auto"
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
        self.out_var.set(str(self.cfg.output_dir()))

    def on_first_show(self) -> None:
        self.console.append("💡 本地转写完全离线，不会访问网络。", "step")
        self.console.append("    · 需要先在「环境安装」装好 Whisper 依赖，并在「模型管理」下载模型；", "muted")
        self.console.append("    · 视频文件会先用 FFmpeg 抽 16kHz 单声道音轨，再交给 Whisper。", "muted")

    # ══════════════════════════════════════════════════════════════════
    def _preflight(self) -> bool:
        rep = self.app.report
        if rep is None:
            return True
        fw = rep.get("faster_whisper")
        if fw and fw.status != "ok":
            if messagebox.askyesno(
                "缺少转写依赖",
                "本地转写需要 faster-whisper，当前未安装。\n\n现在去「环境安装」页安装吗？",
                parent=self,
            ):
                self.app.show_page("env")
            return False
        if not model_manager.installed_models() and self._model_value() == "auto":
            if messagebox.askyesno(
                "还没有模型",
                "本地还没有下载任何 Whisper 模型。\n\n现在去「模型管理」页下载吗？（推荐 medium）",
                parent=self,
            ):
                self.app.show_page("models")
            return False
        ff = rep.get("ffmpeg")
        has_video = any(f.kind == "视频" for f in self.files)
        if has_video and ff and ff.status != "ok":
            if not messagebox.askyesno(
                "缺少 FFmpeg",
                "列表里有视频文件，抽取音轨需要 FFmpeg，但未检测到它。\n\n"
                "仍要继续吗？（点「否」可去「环境安装」一键安装）",
                parent=self,
            ):
                self.app.show_page("env")
                return False
        return True

    def _start(self) -> None:
        if not self.files:
            messagebox.showinfo("请先添加文件", "请先添加要转写的音频或视频文件。", parent=self)
            return
        missing = [f for f in self.files if not f.path.is_file()]
        if missing:
            for f in missing:
                self.console.append(f"⚠️ 文件已不存在，已从列表移除：{f.path}", "warn")
            self.files = [f for f in self.files if f.path.is_file()]
            self._render()
            if not self.files:
                return
        py = self.require_python()
        if not py:
            return
        if not paths.project_ready():
            messagebox.showerror("缺少程序文件",
                                 f"找不到 transcribe_file.py：\n{paths.runtime_dir()}", parent=self)
            return
        if not self._preflight():
            return

        self.save_config()
        out_dir = self._output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        task = jobs.local_task(
            py, [f.path for f in self.files], out_dir,
            model=self._model_value(),
            language=_value_of(LANGUAGES, self.lang_var.get(), "auto"),
            timestamps=bool(self.ts_var.get()),
        )
        total = paths.fmt_bytes(media.total_bytes(self.files))
        self.console.append(f"🎙 共 {len(self.files)} 个文件（{total}），输出到：{out_dir}", "step")
        self.console.append("    首次加载模型需要几十秒，请耐心等待。", "muted")
        self.start_task(task, on_done=lambda code: self._on_finished(task, code))

    def _on_finished(self, task: ProcessTask, code: int) -> None:
        data = jobs.parse_result(task.output)
        self.app.refresh_outputs()
        if not data:
            if code not in (0, 130):
                self.console.append("⚠️ 未获取到结构化结果，请查看上方日志。", "warn")
            return

        ok = data.get("success") or []
        errs = data.get("errors") or []

        if ok:
            self.console.append(f"✅ 成功转写 {len(ok)} 个文件：", "success")
            for r in ok:
                self.console.append(
                    f"    · {r.get('title')}  {r.get('char_count', 0)} 字  "
                    f"用时 {r.get('elapsed', 0):.1f}s\n      → {r.get('md_path')}", "success")
            self.console.append("🎉 完成！到「输出管理」页可预览并一键复制给 AI 总结。", "step")

        for e in errs:
            if e.get("error_type") == "whisper_required":
                self.console.append("⚠️ Whisper 环境未就绪：", "warn")
                for m in e.get("missing", []):
                    self.console.append(f"    - {m}", "warn")
                if messagebox.askyesno(
                    "环境未就绪",
                    "本地转写需要 faster-whisper 和模型：\n\n"
                    + "\n".join(f"· {m}" for m in e.get("missing", []))
                    + "\n\n现在去处理吗？",
                    parent=self,
                ):
                    rep = self.app.report
                    item = rep.get("faster_whisper") if rep else None
                    self.app.show_page("env" if not (item and item.status == "ok") else "models")
            else:
                self.console.append(f"❌ {e.get('url')}\n    {e.get('error')}", "error")
