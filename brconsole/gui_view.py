# -*- coding: utf-8 -*-
"""界面视图层：只负责把控件摆成 `brc-ui-final.html` 的结构。

`gui.py` 保留业务（渲染、队列、读配置、存档、事件循环）；这里只建控件、摆位置，
并把业务要用的控件句柄挂回 `app`。**不做示例数据、不做骨架开关、不写占位文字**。
"""

import tkinter as tk
from tkinter import ttk

from . import __version__, theme, uikit
from .guimodel import (AFTERMATH_LABELS, DEFAULT_RETRY, DEPTH_KEEP_LABEL,
                       DEVICES, ENGINES, FORMATS, RETRY_CHOICES,
                       SCENE_NEED_READ_LABEL, depth_choices, format_label,
                       retry_note)

TITLE = "blender-render-console"
PAD = {"padx": 6, "pady": 4}
LEFT_PX = 205           # 定稿左栏宽度
TOP_PX = 220            # 定稿右栏上方内容区高度（只露出当前队列项）
LOG_ROWS = 14


def build(app, root):
    """一次性搭出整个窗口；业务回调由 `app` 提供。"""
    _init_vars(app)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    _build_header(root)

    paned = ttk.PanedWindow(root, orient="horizontal")
    paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
    left = ttk.Frame(paned, width=LEFT_PX, style="Side.TFrame")
    right = ttk.Frame(paned, style="Main.TFrame")
    paned.add(left, weight=0)
    paned.add(right, weight=1)
    root.after_idle(lambda: paned.sashpos(0, LEFT_PX))

    _build_left(app, left)
    _build_right(app, right)
    root.bind_all("<MouseWheel>", app._on_mousewheel)
    app._set_segments(app.form.segments)


def _init_vars(app):
    app.v_blend = tk.StringVar()
    app.v_scene = tk.StringVar(value=SCENE_NEED_READ_LABEL)
    app.v_blender = tk.StringVar()
    app.v_output = tk.StringVar()
    app.v_engine = tk.StringVar(value=ENGINES[0][1])
    app.v_samples = tk.StringVar()
    app.v_device = tk.StringVar(value=DEVICES[0][1])
    app.v_w = tk.StringVar()
    app.v_h = tk.StringVar()
    app.v_pct = tk.StringVar()
    app.v_format = tk.StringVar(value=format_label(FORMATS[0][0]))
    app.v_depth = tk.StringVar(value=DEPTH_KEEP_LABEL)
    app.v_resume = tk.BooleanVar(value=True)
    app.v_native = tk.BooleanVar(value=True)
    app.v_retry = tk.StringVar(value=DEFAULT_RETRY)
    app.v_autostart = tk.BooleanVar(value=False)
    app.v_auto_resume = tk.BooleanVar(value=True)
    app.v_aftermath = tk.StringVar(value=AFTERMATH_LABELS[0][0])
    app.v_blender_ver = tk.StringVar()
    app._blender_by_label = {}
    app._blender_candidates = []
    app._collection_vars = {}
    app._collection_objects = {}
    app.hidden_collections = []
    app._pending_countdown = None
    app.right_canvas = None
    app.prog_canvas = None


def _build_header(root):
    head = ttk.Frame(root, style="Header.TFrame")
    head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
    ttk.Label(head, text="B", style="Logo.TLabel", width=2).pack(side="left")
    ttk.Label(head, text=TITLE, style="Title.TLabel").pack(side="left", padx=(6, 10))
    ttk.Label(head, text="v%s · 无头渲染控制台" % __version__,
              style="Muted.TLabel").pack(side="left")


def _module(app, parent, key, icon, title, actions=(), expanded=True):
    card = uikit.ModuleCard(parent, title, icon=icon, actions=actions,
                            expanded=expanded)
    card.pack(fill="x", side="top", padx=(0, 6), pady=(4, 0))
    app._modules[key] = {
        "frame": card, "header": card.header, "body": card.body,
        "toggle": card.toggle, "summary": card.summary_label,
        "card": card, "expanded": card.expanded, "summary_key": key}
    setattr(app, "lbl_sum_" + key, card.summary_label)
    for attr, btn in card.actions.items():
        setattr(app, attr, btn)
    return card.body


def _build_left(app, left):
    # 程序设置贴最底，默认折叠；操作按钮在它上面；中间是队列卡片。
    prog = uikit.ModuleCard(left, "程序设置", "⋯", expanded=False)
    prog.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
    app._modules["program"] = {
        "frame": prog, "header": prog.header, "body": prog.body,
        "toggle": prog.toggle, "summary": prog.summary_label,
        "card": prog, "expanded": False, "summary_key": "program"}
    app.lbl_sum_program = prog.summary_label
    app.lbl_sum_program.configure(wraplength=90)
    pwrap = uikit.ScrollArea(prog.body, height=220)
    app.prog_canvas = pwrap.canvas
    app.prog_inner = pwrap.inner
    pwrap.pack(fill="x")
    _build_program_fields(app, pwrap.inner)

    head = ttk.Frame(left, style="Side.TFrame")
    head.pack(fill="x", padx=8, pady=(6, 2))
    ttk.Label(head, text="☰ 渲染队列", style="Section.TLabel").pack(side="left")
    app.lbl_queue_count = ttk.Label(head, text="0 项", style="Badge.TLabel")
    app.lbl_queue_count.pack(side="right")

    app.lst_queue = uikit.QueueList(left, width=190, height=196)
    app.lst_queue.pack(fill="x", padx=8, pady=2)
    app.lst_queue.bind("<<ListboxSelect>>", app._on_queue_select)
    app._drag_from = None
    app.lst_queue.bind("<ButtonPress-1>", app._on_queue_drag_start)
    app.lst_queue.bind("<B1-Motion>", app._on_queue_drag_move)
    app.lst_queue.bind("<ButtonRelease-1>", app._on_queue_drag_end)

    ttk.Frame(left, style="Side.TFrame").pack(fill="both", expand=True)
    app.lbl_queue = ttk.Label(left, text="", style="Muted.TLabel")
    app.lbl_queue.pack_forget()
    bar = uikit.ActionBar(left, actions=[
        ("add", "＋ 添加工程", app.pick_blend),
        ("remove", "✕", app.on_queue_remove),
        ("up", "↑", lambda: app.on_queue_move(-1)),
        ("down", "↓", lambda: app.on_queue_move(1)),
    ])
    for key, width in (("add", 9), ("remove", 2), ("up", 2), ("down", 2)):
        bar.buttons[key].configure(width=width)
    bar.pack(side="bottom", fill="x", padx=8, pady=(2, 6))


def _build_right(app, right):
    ttk.Label(right,
              text="左侧渲染队列 · 右侧该工程全部信息 · 右下日志常显　|　"
                   "界面演示：使用示例工程数据，不会真正启动 Blender",
              style="Muted.TLabel").pack(
                  side="bottom", fill="x", padx=8, pady=(0, 3))
    _build_log(app, right)

    scroll = uikit.ScrollArea(right, height=TOP_PX)
    scroll.pack(side="top", fill="x")
    app.right_canvas = scroll.canvas
    content = scroll.inner
    _build_overview(app, _module(app, content, "overview", "▤", "当前队列项"))
    _build_project(app, _module(
        app, content, "project", "▣", "工程",
        actions=[("btn_inspect", "读取工程配置", app.on_inspect)]))
    _build_frames(app, _module(
        app, content, "frames", "⏱", "帧范围",
        actions=[("btn_add_seg", "＋ 添加一段", app.on_add_segment)]))
    _build_render(app, _module(app, content, "render", "⚙", "渲染参数"))


def _build_log(app, right):
    log = ttk.Frame(right, style="Card.TFrame")
    log.pack(side="bottom", fill="both", expand=True, padx=(6, 8), pady=(4, 2))
    head = ttk.Frame(log, style="CardHead.TFrame")
    head.pack(fill="x")
    ttk.Label(head, text="◉", style="Icon.TLabel").pack(side="left")
    ttk.Label(head, text="进度与日志", style="Section.TLabel").pack(
        side="left", padx=(4, 8))
    app.lbl_state = ttk.Label(head, text="断点：无", style="Muted.TLabel")
    app.lbl_state.pack(side="left", padx=(0, 12))
    bar = uikit.ActionBar(head, actions=[
        ("start", "开始渲染", app.on_start),
        ("stop", "停止", app.on_stop),
        ("open", "打开输出目录", app.open_output_dir),
        ("clear_state", "清空断点", app.clear_state),
        ("clear_log", "清空日志", app.clear_log),
    ])
    bar.pack(side="right")
    app.btn_start = bar.buttons["start"]
    app.btn_start.configure(style="Accent.TButton")
    app.btn_stop = bar.buttons["stop"]
    app.btn_stop.configure(state="disabled")
    app.btn_clear_state = bar.buttons["clear_state"]

    app.pb = ttk.Progressbar(log, orient="horizontal", mode="determinate",
                             maximum=100)
    app.pb.pack(fill="x", padx=8, pady=(6, 2))
    app.lbl_status = ttk.Label(log, text="就绪", anchor="w", justify="left")
    app.lbl_status.pack(fill="x", padx=8)
    app.txt = tk.Text(log, height=LOG_ROWS, wrap="word", state="disabled",
                      font=("Consolas", 9), relief="flat",
                      **theme.log_widget_colors())
    app.txt.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
    app.vs = ttk.Scrollbar(log, orient="vertical", command=app.txt.yview,
                           style=theme.SCROLL_STYLE)
    app._sb_style = theme.SCROLL_STYLE
    app.txt.configure(yscrollcommand=app._on_log_scroll)
    app.vs.pack(side="right", fill="y", pady=6)


def _build_overview(app, body):
    fields = [("工程", "blend"), ("帧范围", "frames"),
              ("帧数", "count"), ("引擎/采样", "engine"),
              ("设备", "device"), ("分辨率", "res"),
              ("格式/位数", "format"), ("输出", "output"),
              ("渲染时隐藏", "hidden"), ("重试/断点", "retry")]
    for c in range(8):
        body.columnconfigure(c, weight=1, uniform="overview")
    for i, (caption, key) in enumerate(fields):
        col, row = (i // 3) * 2, i % 3
        ttk.Label(body, text=caption, style="Muted.TLabel", width=10).grid(
            row=row, column=col, sticky="nw", padx=(8, 2), pady=(12, 12))
        var = tk.StringVar(value="—")
        ttk.Label(body, textvariable=var, style="Value.TLabel",
                  wraplength=170, justify="left").grid(
                      row=row, column=col + 1, sticky="nw",
                      padx=(2, 8), pady=(12, 12))
        app._overview_tags[key] = var


def _build_project(app, body):
    body.columnconfigure(1, weight=1)
    ttk.Label(body, text="工程 .blend").grid(row=0, column=0, sticky="w", **PAD)
    app.ent_blend = ttk.Entry(body, textvariable=app.v_blend, width=54)
    app.ent_blend.grid(row=0, column=1, columnspan=3, sticky="ew", **PAD)
    app.ent_blend.bind("<FocusOut>", lambda _e: app.maybe_inspect())
    ttk.Label(body, text="场景").grid(row=1, column=0, sticky="w", **PAD)
    app.cmb_scene = ttk.Combobox(body, textvariable=app.v_scene, width=26,
                                 state="disabled", values=[SCENE_NEED_READ_LABEL])
    app.cmb_scene.grid(row=1, column=1, sticky="w", **PAD)
    app.cmb_scene.bind("<<ComboboxSelected>>", app.on_scene_change)
    ttk.Button(body, text="读取当前工程的集合状态", style="Small.TButton",
               command=lambda: app.on_inspect(force=True)).grid(
                   row=2, column=1, sticky="w", **PAD)
    app.lbl_collections = ttk.Label(
        body, text="读取工程配置后可勾选；勾选＝渲染时显示，取消＝渲染时隐藏",
        style="Muted.TLabel")
    app.lbl_collections.grid(row=3, column=1, columnspan=3, sticky="w", **PAD)
    app.col_box = ttk.Frame(body, style="Card.TFrame")
    app.col_box.grid(row=4, column=1, columnspan=3, sticky="ew", **PAD)
    app.lbl_inspect = ttk.Label(
        body, text="选好工程后会自动读一次（引擎/采样/分辨率/帧范围/输出路径）",
        style="Muted.TLabel", wraplength=520, justify="left")
    app.lbl_inspect.grid(row=5, column=1, columnspan=3, sticky="w", **PAD)


def _build_frames(app, body):
    head = ttk.Frame(body, style="Card.TFrame")
    head.pack(fill="x")
    for col, (caption, width) in enumerate(
            (("段 / 帧数", 15), ("起帧", 7), ("止帧", 7),
             ("步长", 6), ("", 3))):
        ttk.Label(head, text=caption, width=width, style="Muted.TLabel",
                  anchor="w").grid(row=0, column=col, sticky="w", padx=2)
    app.seg_rows = ttk.Frame(body, style="Card.TFrame")
    app.seg_rows.pack(fill="x")
    app.lbl_frames = ttk.Label(body, text="", style="Muted.TLabel",
                               justify="left", wraplength=560)
    app.lbl_frames.pack(fill="x", pady=(3, 0))
    app.seg_box = body
    body.bind("<Configure>", app._on_seg_box_resize)


def _field(parent, row, col, span, caption, factory):
    cell = ttk.Frame(parent, style="Card.TFrame")
    cell.grid(row=row, column=col, columnspan=span, sticky="ew", padx=4, pady=3)
    ttk.Label(cell, text=caption, style="Muted.TLabel").pack(anchor="w")
    widget = factory(cell)
    widget.pack(fill="x")
    return widget


def _build_render(app, body):
    for c in range(12):
        body.columnconfigure(c, weight=1, uniform="render")
    _field(body, 0, 0, 6, "引擎", lambda p: ttk.Combobox(
        p, textvariable=app.v_engine, state="readonly",
        values=[v for _, v in ENGINES]))
    _field(body, 0, 6, 6, "设备", lambda p: ttk.Combobox(
        p, textvariable=app.v_device, state="readonly",
        values=[v for _, v in DEVICES]))

    def res(p):
        f = ttk.Frame(p, style="Card.TFrame")
        ttk.Entry(f, textvariable=app.v_w, width=6).pack(side="left")
        ttk.Label(f, text="×", style="Muted.TLabel").pack(side="left", padx=2)
        ttk.Entry(f, textvariable=app.v_h, width=6).pack(side="left")
        ttk.Label(f, text="缩放 %", style="Muted.TLabel").pack(
            side="left", padx=(5, 2))
        ttk.Entry(f, textvariable=app.v_pct, width=5).pack(side="left")
        return f

    _field(body, 1, 0, 4, "分辨率", res)
    _field(body, 1, 4, 2, "采样", lambda p: ttk.Entry(
        p, textvariable=app.v_samples, width=7))
    app.cmb_format = _field(body, 1, 6, 4, "格式", lambda p: ttk.Combobox(
        p, textvariable=app.v_format, state="readonly",
        values=[format_label(s[0]) for s in FORMATS]))
    app.cmb_format.bind("<<ComboboxSelected>>", app._on_format_changed)
    uikit.fit_combobox(app.cmb_format)
    app.cmb_depth = _field(body, 1, 10, 2, "位数", lambda p: ttk.Combobox(
        p, textvariable=app.v_depth, state="disabled",
        values=depth_choices(FORMATS[0][0])))
    uikit.fit_combobox(app.cmb_depth)

    def out(p):
        f = ttk.Frame(p, style="Card.TFrame")
        ttk.Entry(f, textvariable=app.v_output).pack(
            side="left", fill="x", expand=True)
        ttk.Button(f, text="选择目录", style="Small.TButton",
                   command=app.pick_output).pack(side="left", padx=(4, 0))
        return f

    _field(body, 2, 0, 9, "输出目录", out)
    ttk.Checkbutton(body, text="从断点续跑", variable=app.v_resume).grid(
        row=2, column=9, columnspan=3, sticky="w", padx=4, pady=(16, 3))


def _build_program_fields(app, body):
    body.columnconfigure(1, weight=1)
    ttk.Label(body, text="Blender 程序").grid(row=0, column=0, sticky="w", **PAD)
    ttk.Entry(body, textvariable=app.v_blender, width=30).grid(
        row=0, column=1, columnspan=2, sticky="ew", **PAD)
    ttk.Button(body, text="自动探测", style="Small.TButton",
               command=app.autofill_blender).grid(row=0, column=3, **PAD)
    ttk.Label(body, text="Blender 版本").grid(row=1, column=0, sticky="w", **PAD)
    app.cmb_blender_ver = ttk.Combobox(body, textvariable=app.v_blender_ver,
                                       width=24, state="readonly", values=[])
    app.cmb_blender_ver.grid(row=1, column=1, columnspan=3, sticky="w", **PAD)
    app.cmb_blender_ver.bind("<<ComboboxSelected>>", app.on_blender_version)
    ttk.Checkbutton(body, text="显示 Blender 原生输出（同类进度行自动折叠）",
                    variable=app.v_native).grid(
                        row=2, column=1, columnspan=3, sticky="w", **PAD)
    ttk.Label(body, text="失败重试").grid(row=3, column=0, sticky="w", **PAD)
    app.cmb_retry = ttk.Combobox(body, textvariable=app.v_retry,
                                 state="readonly", values=list(RETRY_CHOICES))
    app.cmb_retry.grid(row=3, column=1, columnspan=2, sticky="w", **PAD)
    uikit.fit_combobox(app.cmb_retry, list(RETRY_CHOICES))
    app.cmb_retry.bind("<<ComboboxSelected>>", lambda _e: app._sync_retry_note())
    app.lbl_retry_note = ttk.Label(body, text=retry_note(DEFAULT_RETRY),
                                   style="Muted.TLabel", wraplength=200,
                                   justify="left")
    app.lbl_retry_note.grid(row=4, column=1, columnspan=3, sticky="w", **PAD)
    app.chk_autostart = ttk.Checkbutton(
        body, text="开机自动启动", variable=app.v_autostart,
        command=app.on_toggle_autostart)
    app.chk_autostart.grid(row=5, column=1, columnspan=3, sticky="w", **PAD)
    app.chk_auto_resume = ttk.Checkbutton(
        body, text="启动时自动续跑未完成任务", variable=app.v_auto_resume,
        command=app.on_toggle_auto_resume)
    app.chk_auto_resume.grid(row=6, column=1, columnspan=3, sticky="w", **PAD)
    app.lbl_autostart = ttk.Label(body, text="", style="Muted.TLabel",
                                  wraplength=200, justify="left")
    app.lbl_autostart.grid(row=7, column=1, columnspan=3, sticky="w", **PAD)
    ttk.Label(body, text="任务结束后").grid(row=8, column=0, sticky="w", **PAD)
    app.cmb_aftermath = ttk.Combobox(
        body, textvariable=app.v_aftermath, width=18, state="readonly",
        values=[l for l, _ in AFTERMATH_LABELS])
    app.cmb_aftermath.grid(row=8, column=1, columnspan=3, sticky="w", **PAD)
