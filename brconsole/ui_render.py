# -*- coding: utf-8 -*-
"""「渲染参数」模块——照 `tpl-render` 与 `.rgrid/.rfield/.rcontrol` CSS 做这一个模块。

三行 12 列栅格：6+6 / 4+2+4+2 / 9+3；行距 9px、列距 12px；
分辨率宽高 58px、缩放 50px；输出行按钮与「从断点续跑」同排。
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit
from .guimodel import (DEPTH_KEEP_LABEL, DEVICES, ENGINES, FORMATS,
                       FORMAT_KEEP_LABEL, depth_choices, format_label)

#: 下拉里「保持工程设置…」在主界面太占宽，显示时压短（存值仍是完整文案）
_SHORT_FORMAT = {FORMAT_KEEP_LABEL: "保持工程设置"}
_KEEP_ALL = {"保持工程设置（用 .blend 里的）": "保持工程设置",
             "保持工程设置": "保持工程设置"}


def _display_pairs(choices, shorten=None):
    """`(显示文案, 实际值)` 常量表 → `[(显示, 存值)]`，可选把个别文案换短。"""
    shorten = shorten or {}
    return [(shorten.get(label, label), value) for label, value in choices]


class RenderModule(uikit.ModuleCard):
    def __init__(self, master, on_choose_dir=None, vars=None,
                 resume_var=None, **kw):
        super().__init__(master, "渲染参数", icon="⚙",
                         summary="", expanded=True,
                         show_toggle=False, **kw)
        body = self.body
        for c in range(12):
            body.columnconfigure(c, weight=1, uniform="render")
        self.vars = vars if vars is not None else {}
        for key, default in (("engine", ENGINES[0][1]), ("device", DEVICES[0][1]),
                             ("width", ""), ("height", ""), ("pct", ""),
                             ("samples", ""), ("format", format_label(FORMATS[0][0])),
                             ("depth", DEPTH_KEEP_LABEL), ("output", "")):
            self.vars.setdefault(key, tk.StringVar(value=default))

        # ---- 第 1 行：引擎 6 / 设备 6 ----
        self.vars["engine"].set(self.vars["engine"].get() or ENGINES[0][1])
        self.engine_combo = self._field(
            body, 0, 0, 6, "引擎", lambda p: self._mapped_combo(
                p, self.vars["engine"], _display_pairs(ENGINES, _KEEP_ALL)))
        self.device_combo = self._field(
            body, 0, 6, 6, "设备", lambda p: self._mapped_combo(
                p, self.vars["device"], _display_pairs(DEVICES, _KEEP_ALL)))

        # ---- 第 2 行：分辨率 4 / 采样 2 / 格式 4 / 位数 2 ----
        self._field(body, 1, 0, 4, "分辨率", self._resolution)
        self._field(body, 1, 4, 2, "采样", lambda p: ttk.Entry(
            p, textvariable=self.vars["samples"]))
        fmt_pairs = [(_SHORT_FORMAT.get(format_label(s[0]), format_label(s[0])),
                      format_label(s[0])) for s in FORMATS]
        self.format_combo = self._field(
            body, 1, 6, 4, "格式", lambda p: self._mapped_combo(
                p, self.vars["format"], fmt_pairs))
        self.depth_combo = self._field(
            body, 1, 10, 2, "位数", lambda p: ttk.Combobox(
                p, textvariable=self.vars["depth"], state="disabled",
                values=depth_choices(FORMATS[0][0])))

        # ---- 第 3 行：输出目录 9 / 从断点续跑 3 ----
        def out(p):
            f = ttk.Frame(p, style="Card.TFrame")
            ttk.Entry(f, textvariable=self.vars["output"]).pack(
                side="left", fill="x", expand=True)
            uikit.RoundButton(f, text="选择目录", height=33,
                              command=on_choose_dir or (lambda: None)).pack(
                                  side="left", padx=(6, 0))
            return f

        self._field(body, 2, 0, 9, "输出目录", out)
        self.resume_var = resume_var or tk.BooleanVar(value=True)
        # 用同高的占位标签把续跑框顶到和输入框同一条基线，框高固定 30px
        resume_cell = ttk.Frame(body, style="Card.TFrame")
        resume_cell.grid(row=2, column=9, columnspan=3, sticky="ew",
                         padx=(0, 0), pady=(7, 0))
        ttk.Label(resume_cell, text=" ", style="Muted.TLabel").pack(anchor="w")
        box = tk.Frame(resume_cell, bg="#242429", height=33,
                       highlightthickness=1,
                       highlightbackground=theme.BORDER)
        box.pack(fill="x", pady=(4, 0))
        box.pack_propagate(False)
        ttk.Checkbutton(box, text="从断点续跑",
                        variable=self.resume_var).pack(
                            fill="both", expand=True, padx=8)

    def _mapped_combo(self, parent, var, pairs):
        """显示文案 ↔ 存值 分离的下拉。

        存值仍是业务认的标识（`""` / `CYCLES` / `PNG`…），界面却显示人话；
        这样「保持工程设置」不会再显示成一个空下拉，也不会把长文案挤爆格子。
        """
        labels = [label for label, _ in pairs]
        disp = tk.StringVar()

        def to_display(*_a):
            cur = var.get()
            disp.set(next((label for label, value in pairs if value == cur),
                          labels[0] if labels else ""))

        def to_stored(_e=None):
            label = disp.get()
            var.set(next((value for l, value in pairs if l == label), ""))

        to_display()
        cb = ttk.Combobox(parent, textvariable=disp, state="readonly",
                          values=labels)
        cb.bind("<<ComboboxSelected>>", to_stored, add="+")
        var.trace_add("write", to_display)
        cb.display_var = disp
        return cb

    def _field(self, parent, row, col, span, caption, factory):
        cell = ttk.Frame(parent, style="Card.TFrame")
        cell.grid(row=row, column=col, columnspan=span, sticky="ew",
                  padx=(0, 12), pady=(7, 0))
        ttk.Label(cell, text=caption, style="Muted.TLabel").pack(anchor="w")
        widget = factory(cell)
        widget.pack(fill="x", pady=(4, 0))
        return widget

    def _resolution(self, parent):
        f = ttk.Frame(parent, style="Card.TFrame")
        ttk.Entry(f, textvariable=self.vars["width"], width=6).pack(side="left")
        ttk.Label(f, text="×", style="Muted.TLabel").pack(side="left", padx=5)
        ttk.Entry(f, textvariable=self.vars["height"], width=6).pack(side="left")
        ttk.Label(f, text="缩放 %", style="Muted.TLabel").pack(
            side="left", padx=(8, 5))
        ttk.Entry(f, textvariable=self.vars["pct"], width=5).pack(side="left")
        return f
