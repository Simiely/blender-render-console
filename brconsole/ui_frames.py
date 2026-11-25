# -*- coding: utf-8 -*-
"""「帧范围」模块——照 `tpl-frames` 与 `.frames-settings` CSS 做这一个模块。

表头和数据行放在**同一个 grid** 里，列宽完全一致，名称才对得上。
步长用 Spinbox（上下箭头每点一次 ±1）。
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit
from .guimodel import segments_to_frames

#: 7 列：段 / 起帧 100 / 止帧 100 / 步长(输入40+箭头) / 删除 / 间隔100 / 总帧数
COLS = (150, 100, 100, 72, 28)


class FramesModule(uikit.ModuleCard):
    def __init__(self, master, on_add=None, on_remove=None, **kw):
        super().__init__(
            master, "帧范围", icon="⏱", summary="",
            expanded=True, show_toggle=False,
            actions=[("add_seg", "＋ 添加一段", on_add or (lambda: None))],
            **kw)
        self._on_remove = on_remove
        self.rows = []
        self.badge = tk.Label(self.header, text="0 帧", bg="#333338",
                              fg=theme.MUTED, font=("Microsoft YaHei", 8),
                              padx=7, pady=1)
        self.badge.pack(side="right", padx=(0, 6), pady=2)

        # 表头与所有段行共用这个 grid —— 列宽由同一套 columnconfigure 决定
        self.grid = ttk.Frame(self.body, style="Card.TFrame")
        self.grid.pack(fill="x")
        for i, w in enumerate(COLS):
            self.grid.columnconfigure(i, minsize=w, weight=0)
        for i, text in enumerate(("段 / 帧数", "起帧", "止帧", "步长", "")):
            ttk.Label(self.grid, text=text, style="Muted.TLabel",
                      anchor="w").grid(
                          row=0, column=i, sticky="ew", padx=(0, 8), pady=(2, 5))

    def add_segment(self, start="", end="", step="1", focus=False):
        idx = len(self.rows)
        r = idx + 1                         # 第 0 行是表头，段行紧接其后
        widgets = []
        segno = ttk.Frame(self.grid, style="Card.TFrame")
        segno.grid(row=r, column=0, sticky="w", padx=(0, 8), pady=5)
        label = ttk.Label(segno, text="段 %d" % (idx + 1),
                          foreground=theme.ACCENT, background=theme.PANEL2,
                          font=("Microsoft YaHei", 9, "bold"))
        label.pack(side="left")
        count = ttk.Label(segno, text="—", style="Muted.TLabel")
        count.pack(side="left", padx=(6, 0))
        widgets.append(segno)

        vars_ = {}
        entries = {}
        for col, (key, value, width) in enumerate(
                (("start", start, 10), ("end", end, 10)), start=1):
            var = tk.StringVar(value=str(value))
            ent = ttk.Entry(self.grid, textvariable=var, width=width)
            ent.grid(row=r, column=col, sticky="ew", padx=(0, 8), pady=5)
            var.trace_add("write", lambda *_a: self.refresh_counts())
            vars_[key] = var
            entries[key] = ent
            widgets.append(ent)
        step_var = tk.StringVar(value=str(step))
        step_cell = ttk.Frame(self.grid, style="Card.TFrame")
        step_cell.grid(row=r, column=3, sticky="ew", padx=(0, 8), pady=5)
        step_entry = ttk.Entry(step_cell, textvariable=step_var, width=4)
        step_entry.pack(side="left", fill="x", expand=True)
        arrows = ttk.Frame(step_cell, style="Card.TFrame")
        arrows.pack(side="left", padx=(2, 0), fill="y")
        for text, delta in (("▲", 1), ("▼", -1)):
            btn = uikit.RoundButton(
                arrows, text=text, width=18, height=15, radius=4, padx=0,
                font=("Segoe UI Symbol", 7),
                command=lambda v=step_var, d=delta: self._bump(v, d))
            btn.pack(side="top", fill="x", pady=(0, 1))
        step_var.trace_add("write", lambda *_a: self.refresh_counts())
        vars_["step"] = step_var
        entries["step"] = step_entry
        widgets.append(step_cell)

        btn = uikit.RoundButton(self.grid, text="✕", width=28, height=33)
        btn.grid(row=r, column=4, sticky="ns", pady=5)
        widgets.append(btn)

        handle = {"row": r, "label": label, "widgets": widgets,
                  "count": count, "vars": vars_, "entries": entries}
        btn.configure(command=lambda h=handle: self._remove_handle(h))
        self.rows.append(handle)
        self.refresh_counts()
        if focus:
            entries["start"].focus_set()
        return handle

    def _remove_handle(self, handle):
        if handle not in self.rows:
            return
        self.rows.remove(handle)
        for w in handle["widgets"]:
            w.destroy()
        # 重新编号并重排：仍在同一个 grid 里，列对齐不会丢
        for i, h in enumerate(self.rows):
            r = i + 1
            h["row"] = r
            h["label"].configure(text="段 %d" % (i + 1))
            for col, w in enumerate(h["widgets"]):
                w.grid_configure(row=r, column=col)
        self.refresh_counts()
        if self._on_remove:
            self._on_remove(handle)

    @staticmethod
    def _bump(var, delta):
        try:
            value = int(str(var.get()).strip() or "1")
        except ValueError:
            value = 1
        var.set(str(max(1, value + delta)))

    def refresh_counts(self):
        """每行最右侧显示**累计帧数**：段 1=120，段 2=120+31=151。"""
        total = 0
        for row in self.rows:
            seg = {k: v.get() for k, v in row["vars"].items()}
            frames, _errors = segments_to_frames([seg])
            if frames:
                total += len(frames)
                row["count"].configure(text="%d 帧" % len(frames),
                                       style="OK.TLabel")
            else:
                row["count"].configure(text="—", style="Muted.TLabel")
        self.badge.configure(text="%d 帧" % total)

    def read_segments(self):
        return [{k: v.get() for k, v in row["vars"].items()} for row in self.rows]

    def set_segments(self, segments):
        for handle in list(self.rows):
            for w in handle["widgets"]:
                w.destroy()
        self.rows = []
        for seg in (list(segments) or [{"start": "", "end": "", "step": "1"}]):
            self.add_segment(seg.get("start", ""), seg.get("end", ""),
                             seg.get("step", "1"))
        self.refresh_counts()

    def remove_handle(self, handle):
        self._remove_handle(handle)
