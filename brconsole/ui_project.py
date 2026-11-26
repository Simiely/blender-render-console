# -*- coding: utf-8 -*-
"""「工程」模块——只照 `tpl-project` 和项目专属 CSS 做这一个模块。

取自定稿 HTML 的三条规则：
  .cmod[data-mod="project"] .field{grid-template-columns:104px 1fr}
  .cmod[data-mod="project"] .colgrid{repeat(auto-fill,minmax(200px,1fr));gap:4px 12px}
  .blend-input{flex:1 1 340px!important;min-width:220px}
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit
from .guimodel import SCENE_NEED_READ_LABEL


class ProjectModule(uikit.ModuleCard):
    """工程模块：.blend + 浏览 + 场景 / 集合状态 / 集合显示 / 读取回显。"""

    def __init__(self, master, on_browse=None, on_inspect=None,
                 on_read_collections=None, on_scene_change=None,
                 on_blend_commit=None, blend_var=None, scene_var=None,
                 **kw):
        super().__init__(
            master, "工程", icon="▣", summary="",
            expanded=True, show_toggle=False,
            actions=[("inspect", "读取工程配置", on_inspect or (lambda: None))],
            **kw)
        body = self.body
        body.columnconfigure(1, weight=1)
        self.on_browse = on_browse
        self.on_read_collections = on_read_collections

        self.inspect_status = tk.StringVar(
            value="选好工程后会自动读一次（引擎/采样/分辨率/帧范围/输出路径）")

        # ---- field 1: 工程 .blend（104px 标签；输入框 flex 340；场景 select 150）----
        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(4, 2))
        ttk.Label(row1, text="工程 .blend", width=13,
                  foreground="#c9c9d0", background=theme.PANEL2).pack(side="left")
        self.blend_var = blend_var or tk.StringVar()
        self.blend_entry = ttk.Entry(row1, textvariable=self.blend_var, width=44)
        self.blend_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        if on_blend_commit:
            self.blend_entry.bind("<FocusOut>", lambda _e: on_blend_commit())
        uikit.RoundButton(row1, text="浏览…", height=33,
                          command=on_browse or (lambda: None)).pack(side="left")
        ttk.Label(row1, text="场景", style="Muted.TLabel").pack(
            side="left", padx=(8, 4))
        self.scene_var = scene_var or tk.StringVar(
            value=SCENE_NEED_READ_LABEL)
        self.scene_combo = ttk.Combobox(
            row1, textvariable=self.scene_var, width=20, state="disabled",
            values=[SCENE_NEED_READ_LABEL])
        self.scene_combo.pack(side="left")
        if on_scene_change:
            self.scene_combo.bind("<<ComboboxSelected>>", on_scene_change)

        # 读取回显不再单独占一行（业务仍可写这两个变量，只是不展示）
        self.collection_status = tk.StringVar(
            value="未读取（要先读 .blend，才知道哪些集合本身已隐藏）")
        self.lbl_collections = ttk.Label(body,
                                         textvariable=self.collection_status,
                                         style="Muted.TLabel")
        self.lbl_inspect = ttk.Label(body, textvariable=self.inspect_status,
                                     style="Muted.TLabel")

        # ---- field 2: 集合显示（colgrid 每列至少 200px）----
        row3 = ttk.Frame(body, style="Card.TFrame")
        row3.grid(row=2, column=0, columnspan=4, sticky="ew", pady=2)
        ttk.Label(row3, text="集合显示", width=13,
                  foreground="#c9c9d0", background=theme.PANEL2).pack(side="left")
        self.collections_box = ttk.Frame(row3, style="Card.TFrame")
        self.collections_box.pack(side="left", fill="x", expand=True,
                                  padx=(6, 0))
        self.collection_vars = {}
        # 提示行跟在复选框区下面：没读工程时解释这一栏是干嘛的，读完换成勾选规则。
        # （业务层 configure(text=) 会经由 gui_view_new 的代理落到 collection_status 上）
        self.lbl_collections.grid(row=3, column=0, columnspan=4, sticky="w",
                                  padx=(19, 0), pady=(0, 2))

    def set_scenes(self, names, active=None):
        """填充场景下拉；names 为空时回到「先读取工程配置」。"""
        names = list(names or [])
        if not names:
            self.scene_combo.configure(values=[SCENE_NEED_READ_LABEL],
                                       state="disabled")
            self.scene_var.set(SCENE_NEED_READ_LABEL)
            return
        self.scene_combo.configure(values=names, state="readonly")
        self.scene_var.set(active if active in names else names[0])

    def set_collections(self, collections, hidden=(), on_toggle=None):
        """collections: [{name, objects}]；hidden 是要隐藏的名字（勾选＝显示）。"""
        for w in self.collections_box.winfo_children():
            w.destroy()
        self.collection_vars = {}
        hidden = set(hidden or ())
        for i, item in enumerate(collections):
            name = item.get("name") if isinstance(item, dict) else str(item)
            nobj = item.get("objects") if isinstance(item, dict) else None
            text = "%s（%s 个对象）" % (name, nobj) if nobj is not None else name
            var = tk.BooleanVar(value=(name not in hidden))
            if on_toggle:
                var.trace_add("write", lambda *_a: on_toggle())
            cb = ttk.Checkbutton(self.collections_box, text=text, variable=var)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 12), pady=2)
            self.collection_vars[name] = var

    def hidden_collections(self):
        """界面勾选＝显示；返回仍然是要隐藏的名字列表。"""
        return [name for name, var in self.collection_vars.items() if not var.get()]
