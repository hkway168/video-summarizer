"""
gui/tabs/output_tab.py —— ④ 输出内容管理页

界面能力：
    * 列出输出目录下的全部 transcript.md（标题、平台、来源、时长、体积、时间）
    * 关键词搜索、右侧全文预览
    * 打开文件 / 打开所在目录 / 另存为 / 复制全文 / 只复制文字稿 / 删除
    * 切换输出目录、清理音频缓存
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core import outputs, paths
from ..widgets.theme import COLORS, FONTS, card, card_title
from .base import BaseTab

BODY_RE = re.compile(r"##\s*📖\s*完整文字稿[^\n]*\n(.*)$", re.S)


class OutputTab(BaseTab):
    title = "输出管理"
    subtitle = "所有生成的文字稿都在这里：可预览、复制给 AI 总结、另存或删除"

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, app)
        self.docs: list[outputs.OutputDoc] = []
        self._build()

    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        head = card(self)
        head.pack(fill="x")
        row = ttk.Frame(head, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="输出目录", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.dir_var = tk.StringVar(value=str(self.cfg.output_dir()))
        ttk.Entry(row, textvariable=self.dir_var).pack(side="left", fill="x", expand=True)
        b1 = ttk.Button(row, text="更改…", width=8, command=self._pick_dir)
        b1.pack(side="left", padx=(6, 0))
        b2 = ttk.Button(row, text="打开", width=6,
                        command=lambda: paths.open_in_explorer(self._dir()))
        b2.pack(side="left", padx=(6, 0))
        b3 = ttk.Button(row, text="🔄 刷新", width=8, command=self.refresh)
        b3.pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(head, style="Card.TFrame")
        row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="搜索", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        ent = ttk.Entry(row2, textvariable=self.search_var, width=32)
        ent.pack(side="left")
        self.search_var.trace_add("write", lambda *_: self._render_list())
        self.stat_var = tk.StringVar(value="")
        ttk.Label(row2, textvariable=self.stat_var, style="Muted.TLabel").pack(side="left", padx=(12, 0))
        b4 = ttk.Button(row2, text="🧹 清理音频缓存", command=self._clean)
        b4.pack(side="right")
        self.register_buttons(b1, b2, b3, b4)

        # ── 主体：左列表 + 右预览 ──
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, pady=(12, 0))

        left = card(pane, padding=10)
        cols = ("title", "platform", "source", "size", "time")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for key, text, width, anchor in (
            ("title", "标题", 260, "w"),
            ("platform", "平台", 80, "w"),
            ("source", "来源", 120, "w"),
            ("size", "大小", 80, "e"),
            ("time", "生成时间", 130, "w"),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "title"))
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", lambda e: self._open_file())
        pane.add(left, weight=3)

        right = card(pane, padding=10)
        rhead = ttk.Frame(right, style="Card.TFrame")
        rhead.pack(fill="x")
        self.doc_title = tk.StringVar(value="（未选择文档）")
        ttk.Label(rhead, textvariable=self.doc_title, style="H2.TLabel",
                  wraplength=420, justify="left").pack(anchor="w")
        self.doc_meta = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.doc_meta, style="Muted.TLabel",
                  wraplength=420, justify="left").pack(anchor="w", pady=(4, 8))

        box = tk.Frame(right, bg=COLORS["card"], highlightthickness=1,
                       highlightbackground=COLORS["border"])
        box.pack(fill="both", expand=True)
        self.preview = tk.Text(box, wrap="word", bd=0, relief="flat", padx=10, pady=8,
                               font=FONTS.get("ui", ("Microsoft YaHei UI", 10)),
                               state="disabled", background="#ffffff")
        psb = ttk.Scrollbar(box, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=psb.set)
        psb.pack(side="right", fill="y")
        self.preview.pack(side="left", fill="both", expand=True)

        ops = ttk.Frame(right, style="Card.TFrame")
        ops.pack(fill="x", pady=(10, 0))
        for text, cmd in (
            ("📖 打开文件", self._open_file),
            ("📂 所在目录", self._open_dir),
            ("📋 复制文字稿", self._copy_body),
            ("💾 另存为…", self._save_as),
        ):
            ttk.Button(ops, text=text, command=cmd).pack(side="left", padx=(0, 6), pady=2)
        ttk.Button(ops, text="🗑 删除", style="Danger.TButton",
                   command=self._delete).pack(side="right", pady=2)
        pane.add(right, weight=4)

        self.hint_var = tk.StringVar(
            value="💡 想要 AI 总结：点「复制文字稿」，粘贴给 CodeBuddy / ChatGPT 并说「帮我总结这份视频文字稿」。")
        ttk.Label(self, textvariable=self.hint_var, style="MutedBg.TLabel").pack(anchor="w", pady=(10, 0))

    # ══════════════════════════════════════════════════════════════════
    def on_show(self) -> None:
        super().on_show()
        self.dir_var.set(str(self.cfg.output_dir()))
        self.refresh()

    def _dir(self) -> Path:
        raw = self.dir_var.get().strip()
        return Path(raw) if raw else paths.default_output_dir()

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录", initialdir=str(self._dir()), parent=self)
        if d:
            self.dir_var.set(d)
            self.cfg.set("output_dir", d)
            self.refresh()

    def refresh(self) -> None:
        legacy = paths.runtime_dir() / "output"
        self.docs = outputs.scan_outputs(self._dir(), extra_dirs=[legacy])
        self._render_list()

    def _render_list(self) -> None:
        kw = self.search_var.get().strip().lower()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        shown = 0
        for idx, d in enumerate(self.docs):
            if kw and kw not in d.search_blob():
                continue
            shown += 1
            self.tree.insert("", "end", iid=str(idx), values=(
                d.title, d.platform or "-", d.source or "-", d.size_text, d.mtime_text,
            ))
        total_text = outputs.summarize(self.docs)
        self.stat_var.set(total_text + (f" · 筛选出 {shown} 个" if kw else ""))
        if shown:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
        else:
            self._set_preview("（没有匹配的文档）", "（未选择文档）", "")

    def _current(self) -> outputs.OutputDoc | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return self.docs[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _set_preview(self, text: str, title: str, meta: str) -> None:
        self.doc_title.set(title)
        self.doc_meta.set(meta)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _on_select(self) -> None:
        d = self._current()
        if not d:
            return
        meta = " · ".join(x for x in (
            d.platform, d.author, d.duration, d.source, d.created, d.size_text) if x)
        self._set_preview(outputs.read_text(d.path), d.title, meta)

    # ── 操作 ──────────────────────────────────────────────────────────
    def _open_file(self) -> None:
        d = self._current()
        if d:
            paths.open_file(d.path)

    def _open_dir(self) -> None:
        d = self._current()
        paths.open_in_explorer(d.path if d else self._dir())

    def _copy_body(self) -> None:
        d = self._current()
        if not d:
            return
        text = outputs.read_text(d.path)
        m = BODY_RE.search(text)
        body = (m.group(1) if m else text).strip()
        payload = f"视频标题：{d.title}\n平台：{d.platform}\n作者：{d.author}\n链接：{d.url}\n\n{body}"
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self.hint_var.set(f"✅ 已复制《{d.title}》的文字稿（{len(payload)} 字），粘贴给 AI 即可总结。")
        except tk.TclError:
            messagebox.showerror("复制失败", "无法写入剪贴板。", parent=self)

    def _save_as(self) -> None:
        d = self._current()
        if not d:
            return
        target = filedialog.asksaveasfilename(
            title="另存为", initialfile=d.path.name, defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("文本文件", "*.txt"), ("全部文件", "*.*")],
            parent=self,
        )
        if not target:
            return
        ok, msg = outputs.save_as(d.path, target)
        self.hint_var.set(("✅ " if ok else "❌ ") + msg)

    def _delete(self) -> None:
        d = self._current()
        if not d:
            return
        if not messagebox.askyesno("确认删除", f"确定删除文档？\n\n{d.path}", parent=self):
            return
        ok, msg = outputs.delete_doc(d.path)
        self.hint_var.set(("✅ " if ok else "❌ ") + msg)
        self.refresh()

    def _clean(self) -> None:
        size = outputs.dir_size(paths.downloads_dir())
        if size == 0:
            self.hint_var.set("ℹ️ 音频缓存目录已经是空的。")
            return
        if not messagebox.askyesno(
            "清理缓存",
            f"将删除临时音频/字幕文件（约 {paths.fmt_bytes(size)}）：\n{paths.downloads_dir()}\n\n继续吗？",
            parent=self,
        ):
            return
        n, freed = outputs.clean_downloads(paths.downloads_dir())
        self.hint_var.set(f"✅ 已清理 {n} 个文件，释放 {paths.fmt_bytes(freed)}")
