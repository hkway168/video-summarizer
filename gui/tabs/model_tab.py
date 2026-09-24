"""
gui/tabs/model_tab.py —— ③ 模型管理页

界面能力：
    * 列出全部 Whisper 模型（tiny ~ large-v3）及本地安装状态、占用体积
    * 一键下载 / 重新下载（断点续传，支持 HuggingFace 镜像加速）
    * 删除模型释放磁盘、打开模型目录
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..core import model_manager, paths
from ..widgets.console import ConsoleWidget
from ..widgets.theme import COLORS, card, card_title
from .base import BaseTab

HF_MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
    "",
]


class ModelTab(BaseTab):
    title = "模型管理"
    subtitle = "没有字幕的视频要靠本地 Whisper 模型转写；推荐先下载 medium（约 1.4GB）"

    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, app)
        self._build()

    # ══════════════════════════════════════════════════════════════════
    def _build(self) -> None:
        head = card(self)
        head.pack(fill="x")
        title_box = ttk.Frame(head, style="Card.TFrame")
        title_box.pack(fill="x")
        card_title(title_box, "Whisper 模型",
                   f"存放位置：{paths.models_dir()}").pack(side="left", anchor="w")
        b_open = ttk.Button(title_box, text="📂 打开模型目录",
                            command=lambda: paths.open_in_explorer(paths.models_dir()))
        b_open.pack(side="right")
        b_refresh = ttk.Button(title_box, text="🔄 刷新", width=8, command=self.refresh)
        b_refresh.pack(side="right", padx=(0, 8))

        bar = ttk.Frame(head, style="Card.TFrame")
        bar.pack(fill="x", pady=(10, 0))
        ttk.Label(bar, text="下载镜像", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.mirror_var = tk.StringVar(value=self.cfg.get("hf_mirror"))
        ttk.Combobox(bar, textvariable=self.mirror_var, values=HF_MIRRORS,
                     width=36).pack(side="left")
        ttk.Label(bar, text="（国内保留 hf-mirror.com 会快很多；留空走官方源）",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        self.mirror_var.trace_add(
            "write", lambda *_: self.cfg.set("hf_mirror", self.mirror_var.get().strip()))

        self.total_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.total_var, style="Muted.TLabel").pack(side="right")

        # ── 列表 ──
        listcard = card(self)
        listcard.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("size", "status", "disk", "vram", "desc")
        self.tree = ttk.Treeview(listcard, columns=cols, show="headings",
                                 height=8, selectmode="browse")
        for key, text, width, anchor in (
            ("size", "模型", 110, "w"),
            ("status", "状态", 100, "w"),
            ("disk", "体积", 110, "w"),
            ("vram", "显存需求", 90, "w"),
            ("desc", "说明", 460, "w"),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor)
        sb = ttk.Scrollbar(listcard, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("installed", foreground=COLORS["success"])
        self.tree.tag_configure("broken", foreground=COLORS["warn"])
        self.tree.bind("<Double-1>", lambda e: self._download())

        ops = ttk.Frame(self)
        ops.pack(fill="x", pady=(10, 0))
        self.btn_dl = ttk.Button(ops, text="⬇ 下载选中模型", style="Accent.TButton",
                                 command=self._download)
        self.btn_dl.pack(side="left")
        b_del = ttk.Button(ops, text="🗑 删除选中模型", style="Danger.TButton",
                           command=self._delete)
        b_del.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(ops, text="■ 停止下载", style="Danger.TButton",
                                   command=self.stop_task)
        self.btn_stop.pack(side="right")
        self.register_buttons(self.btn_dl, b_del, b_refresh, b_open)

        self.console = ConsoleWidget(self, title="下载日志", height=9)
        self.console.pack(fill="both", expand=True, pady=(12, 0))
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))

    # ══════════════════════════════════════════════════════════════════
    def on_show(self) -> None:
        super().on_show()
        self.refresh()

    def on_first_show(self) -> None:
        self.console.append("💡 推荐：medium（速度/质量均衡）；显卡好可选 large-v3；"
                            "只想快速试用选 small。", "step")

    def refresh(self) -> None:
        sel = self._selected_size()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for st in model_manager.scan_models():
            tag = "installed" if st.installed else ("broken" if st.broken else "")
            self.tree.insert("", "end", iid=st.size, tags=(tag,), values=(
                st.size, st.status_text, st.size_text, st.spec.vram, st.spec.desc,
            ))
        best = model_manager.best_local_model()
        total = model_manager.total_installed_bytes()
        self.total_var.set(
            (f"已占用 {paths.fmt_bytes(total)} · 默认使用 {best}" if best
             else "尚未安装任何模型")
        )
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)
        elif not self.tree.selection():
            self.tree.selection_set("medium")

    def _selected_size(self) -> str:
        sel = self.tree.selection()
        return sel[0] if sel else ""

    # ══════════════════════════════════════════════════════════════════
    def _download(self) -> None:
        size = self._selected_size()
        if not size:
            messagebox.showinfo("请选择模型", "请先在列表里选择一个模型。", parent=self)
            return
        py = self.require_python()
        if not py:
            return

        rep = self.app.report
        if rep:
            hub = rep.get("huggingface_hub")
            if hub and hub.status != "ok":
                if messagebox.askyesno(
                    "缺少下载依赖",
                    "下载模型需要 huggingface_hub（属于 Whisper 依赖组）。\n\n现在去「环境安装」页安装吗？",
                    parent=self,
                ):
                    self.app.show_page("env")
                return

        spec = model_manager.SPEC_BY_SIZE[size]
        st = {m.size: m for m in model_manager.scan_models()}[size]
        if st.installed and not messagebox.askyesno(
            "重新下载",
            f"{size} 已安装（{st.size_text}）。\n\n要重新下载/校验吗？（已存在的文件会跳过）",
            parent=self,
        ):
            return
        if not st.installed and not messagebox.askyesno(
            "确认下载",
            f"即将下载模型 {size}（{spec.approx}）\n来源：{spec.repo}\n"
            f"目标：{paths.models_dir() / size}\n\n"
            "下载支持断点续传，中断后再次点击会继续。是否开始？",
            parent=self,
        ):
            return

        task = model_manager.download_task(py, size, self.cfg)

        def _done(code: int) -> None:
            self.refresh()
            self.app.refresh_models()
            if code == 0:
                self.console.append(f"🎉 {size} 可用了，回到「视频转写」页即可使用。", "step")

        self.start_task(task, on_done=_done)

    def _delete(self) -> None:
        size = self._selected_size()
        if not size:
            return
        st = {m.size: m for m in model_manager.scan_models()}[size]
        if not st.path.exists():
            messagebox.showinfo("无需删除", f"{size} 尚未下载。", parent=self)
            return
        if not messagebox.askyesno(
            "确认删除",
            f"将删除目录：\n{st.path}\n（释放 {st.size_text}）\n\n确定吗？",
            parent=self,
        ):
            return
        ok, msg = model_manager.delete_model(size)
        self.console.append(("✅ " if ok else "❌ ") + msg, "success" if ok else "error")
        self.refresh()
        self.app.refresh_models()
