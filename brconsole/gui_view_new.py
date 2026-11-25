# -*- coding: utf-8 -*-
"""新界面装配层：顶栏 + 左栏 + 右侧模块 + 日志，全部用确认版零件。

`gui.py` 仍只做业务；这里负责把业务变量/回调注入模块，并暴露旧控件别名，
让已有业务方法不用整体重写。
"""

import time
import tkinter as tk
from tkinter import ttk

from . import theme, uikit
from .guimodel import (AFTERMATH_LABELS, DEFAULT_RETRY, DEPTH_KEEP_LABEL,
                       DEVICES, ENGINES, FORMATS, RETRY_CHOICES,
                       SCENE_NEED_READ_LABEL, depth_choices, format_label,
                       retry_note)
from .ui_frames import FramesModule
from .ui_leftqueue import LeftQueuePanel
from .ui_log import LogPanel
from .ui_overview import OverviewModule
from .ui_project import ProjectModule
from .ui_render import RenderModule


class _LabelProxy(object):
    def __init__(self, panel, kind):
        self.panel = panel
        self.kind = kind

    def configure(self, **kw):
        text = kw.get("text")
        if text is None:
            return
        if self.kind == "retry":
            self.panel.set_retry_note(text)
        else:
            self.panel.set_autostart_note(text)

    def cget(self, _key):
        if _key == "text":
            return (self.panel.retry_note_text if self.kind == "retry"
                    else self.panel.autostart_note_text)
        return ""


class _StatusProxy(object):
    """业务的 configure(text=) 落到模块里的一个 StringVar（提示行随之更新）。"""

    def __init__(self, var):
        self.var = var

    def configure(self, **kw):
        text = kw.get("text")
        if text is not None:
            self.var.set(text)

    def cget(self, _key):
        return self.var.get() if _key == "text" else ""


class _CheckProxy(object):
    def __init__(self, panel, key):
        self.panel = panel
        self.key = key

    def configure(self, **kw):
        if "state" in kw:
            # 置灰只影响弹窗里那个勾选框，别顺手把存档状态改掉
            self.panel.set_disabled(self.key, kw["state"] == "disabled")


class _ComboProxy(object):
    def __init__(self, panel, kind):
        self.panel = panel
        self.kind = kind

    def configure(self, **kw):
        if "values" in kw and self.kind == "version":
            self.panel.set_versions(kw["values"])

    def cget(self, key):
        if key == "values":
            return self.panel.version_values if self.kind == "version" else list(RETRY_CHOICES)
        if key == "font":
            return ("Microsoft YaHei", 9)
        return ""


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


def _hidden(parent, text=""):
    lbl = ttk.Label(parent, text=text, style="Muted.TLabel")
    lbl.pack_forget()
    return lbl


def _stack(content, module, first=False, last=False):
    """模块按设计稿 `.stack{padding:12px}` + `.cmod{margin-bottom:10px}` 排。

    间距露出来的是 scroll.inner 的面板色（PANEL #252526）—— 不是窗口底色
    （BG #171719，那才是之前看着像"黑边"的东西）。模块之间不再画分隔线：
    设计稿里卡片靠间距断开，不靠线。
    """
    module.pack(fill="x", padx=12,
                pady=(12 if first else 0, 12 if last else 10))
    return module


def build(app, root):
    _init_vars(app)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    main = tk.Frame(root, bg=theme.PANEL)
    main.grid(row=0, column=0, sticky="nsew")
    main.rowconfigure(0, weight=1)
    main.columnconfigure(1, weight=1)

    # ---------------- 左栏 ----------------
    left = LeftQueuePanel(
        main, on_add=app.pick_blend, on_remove=app.on_queue_remove,
        on_up=lambda: app.on_queue_move(-1),
        on_down=lambda: app.on_queue_move(1),
        on_reorder=app.on_queue_reorder,
        on_detect=app.autofill_blender,
        on_version=app.on_blender_version,
        on_retry=lambda _e=None: app._sync_retry_note(),
        on_autostart=app.on_toggle_autostart,
        on_auto_resume=app.on_toggle_auto_resume)
    left.grid(row=0, column=0, sticky="nsw")
    app.lst_queue = left
    app.lbl_queue_count = left.count
    app.lbl_queue = _hidden(root)
    app.lst_queue.bind("<<ListboxSelect>>", app._on_queue_select)

    # ---------------- 右栏 ----------------
    right = tk.Frame(main, bg=theme.PANEL)
    right.grid(row=0, column=1, sticky="nsew")
    app.right = right

    log = LogPanel(
        right, on_start=app.on_start, on_stop=app.on_stop,
        on_open=app.open_output_dir, on_clear_state=app.clear_state,
        on_clear_log=app.clear_log, demo=getattr(app, "demo", False))
    log.pack(side="bottom", fill="x")
    app.log_panel = log
    # 旧业务直接用这几个句柄：Text / 进度 / 状态行 / 断点行 / 三个按钮
    app.txt = log.text
    app.pb = log.pb
    app.prog_canvas = log.track
    app.lbl_status = log.lbl_status
    app.lbl_state = log.lbl_state
    app.btn_start = log.btn_start
    app.btn_stop = log.btn_stop
    app.btn_clear_state = log.btn_clear_state

    scroll = uikit.ScrollArea(right)
    scroll.pack(fill="both", expand=True)
    app.right_canvas = scroll.canvas
    content = scroll.inner

    overview = _stack(content, OverviewModule(content), first=True)
    app.overview = overview
    app._overview_tags = dict(overview.fields)
    app._overview_tags["frames"] = overview.fields["range"]

    project = _stack(content, ProjectModule(
        content, on_browse=app.pick_blend, on_inspect=app.on_inspect,
        on_scene_change=app.on_scene_change, on_blend_commit=app.maybe_inspect,
        blend_var=app.v_blend, scene_var=app.v_scene))
    app.project = project
    app.ent_blend = project.blend_entry
    app.cmb_scene = project.scene_combo
    app.btn_inspect = project.actions["inspect"]
    app.col_box = project.collections_box
    # 「集合显示」下的提示行是模块真实控件：业务 configure(text=) 写进它的变量
    app.lbl_collections = _StatusProxy(project.collection_status)
    app.lbl_inspect = _hidden(root)
    app.lbl_scene = _hidden(root)

    frames = _stack(content, FramesModule(content, on_add=app.on_add_segment))
    app.frames_module = frames
    app.seg_rows = frames.grid
    app.lbl_frames = _hidden(root)

    render = _stack(content, RenderModule(
        content, on_choose_dir=app.pick_output,
        vars={"engine": app.v_engine, "device": app.v_device,
              "width": app.v_w, "height": app.v_h, "pct": app.v_pct,
              "samples": app.v_samples, "format": app.v_format,
              "depth": app.v_depth, "output": app.v_output},
        resume_var=app.v_resume), last=True)
    render.format_combo.bind("<<ComboboxSelected>>", app._on_format_changed,
                             add="+")
    app.render = render
    app.cmb_format = render.format_combo
    app.cmb_depth = render.depth_combo

    prog = left.program
    app.program = prog
    app.v_blender = prog.vars["blender"]
    app.v_blender_ver = prog.vars["blender_ver"]
    app.v_native = prog.vars["native"]
    app.v_retry = prog.vars["retry"]
    app.v_autostart = prog.vars["autostart"]
    app.v_auto_resume = prog.vars["auto_resume"]
    app.v_aftermath = prog.vars["aftermath"]
    app.cmb_blender_ver = _ComboProxy(prog, "version")
    app.cmb_retry = _ComboProxy(prog, "retry")
    app.chk_autostart = _CheckProxy(prog, "autostart")
    app.chk_auto_resume = _CheckProxy(prog, "auto_resume")
    app.lbl_retry_note = _LabelProxy(prog, "retry")
    app.lbl_autostart = _LabelProxy(prog, "autostart")

    # 旧业务仍会 configure 摘要 Label；新界面不显示摘要，用隐藏 Label 吸收。
    app._modules = {
        "overview": {"card": overview, "expanded": True, "summary": None},
        "project": {"card": project, "expanded": True, "summary": None},
        "frames": {"card": frames, "expanded": True, "summary": None},
        "render": {"card": render, "expanded": True, "summary": None},
        "program": {"card": prog, "expanded": False, "summary": None},
    }
    for key in ("overview", "project", "frames", "render", "program"):
        setattr(app, "lbl_sum_" + key, _hidden(root))
    app.lbl_sum_program.configure(text="")

    # 初始化控件状态（业务方法依赖这些）
    app.program.set_versions([])
    app.program.vars["retry"].set(DEFAULT_RETRY)
    app.program.set_retry_note(retry_note(DEFAULT_RETRY))


_VAR_ATTRS = {
    "blend": "v_blend", "scene": "v_scene", "blender": "v_blender",
    "output": "v_output", "engine": "v_engine", "samples": "v_samples",
    "device": "v_device", "width": "v_w", "height": "v_h", "pct": "v_pct",
    "format": "v_format", "depth": "v_depth", "resume": "v_resume",
    "native": "v_native", "retry": "v_retry", "autostart": "v_autostart",
    "auto_resume": "v_auto_resume", "aftermath": "v_aftermath",
    "blender_ver": "v_blender_ver",
}

_CONTROL_ATTRS = {
    "blender_ver": "cmb_blender_ver", "retry": "cmb_retry",
    "depth": "cmb_depth", "scene": "cmb_scene", "inspect": "btn_inspect",
    "start": "btn_start", "stop": "btn_stop",
    "clear_state": "btn_clear_state",
    "status": "lbl_status", "state": "lbl_state",
    "retry_note": "lbl_retry_note", "autostart_note": "lbl_autostart",
    "collection_status": "lbl_collections", "inspect_note": "lbl_inspect",
    "scene_note": "lbl_scene", "frames_preview": "lbl_frames",
    "queue_count": "lbl_queue_count", "queue": "lbl_queue",
}

_TEXT_KEYS = {
    "status": "lbl_status", "state": "lbl_state",
    "retry_note": "lbl_retry_note", "autostart_note": "lbl_autostart",
    "collection_status": "lbl_collections", "inspect_note": "lbl_inspect",
    "scene_note": "lbl_scene", "frames_preview": "lbl_frames",
    "queue_count": "lbl_queue_count", "queue": "lbl_queue",
}


class TkView(object):
    """`brconsole.view_contract.ViewProtocol` 的 Tk 实现。

    旧业务仍可直接读 `app.v_*` / `app.lbl_*`（兼容期），但新控制器只走这里。
    `build()` 保持原样：它仍然把控件别名挂到 app 上，避免一次性大改。
    """

    def __init__(self, app, root):
        self.app = app
        self.root = root
        build(app, root)

    # ---------- 变量 ----------
    def var(self, name):
        return getattr(self.app, _VAR_ATTRS[name])

    def get(self, name):
        return self.var(name).get()

    def set(self, name, value):
        self.var(name).set(value)

    def trace(self, name, mode, callback):
        return self.var(name).trace_add(mode, callback)

    # ---------- 控件 ----------
    def configure(self, name, **kw):
        getattr(self.app, _CONTROL_ATTRS.get(name, name)).configure(**kw)

    def cget(self, name, key):
        return getattr(self.app, _CONTROL_ATTRS.get(name, name)).cget(key)

    def _text_widget(self, key):
        if key.startswith("summary."):
            return getattr(self.app, "lbl_sum_" + key.split(".", 1)[1])
        if key.startswith("overview."):
            return self.app._overview_tags[key.split(".", 1)[1]]
        return getattr(self.app, _TEXT_KEYS[key])

    def set_text(self, key, text, **kw):
        self._text_widget(key).configure(text=text, **kw)

    def text(self, key):
        return self._text_widget(key).cget("text")

    # ---------- 下拉 ----------
    def set_versions(self, values):
        self.configure("blender_ver", values=list(values))

    def set_retry_values(self, values):
        self.configure("retry", values=list(values))

    def set_depth_values(self, values, disabled=False):
        self.configure("depth", values=list(values),
                       state="disabled" if disabled else "readonly")

    def set_scene_values(self, values, disabled=False):
        self.configure("scene", values=list(values),
                       state="disabled" if disabled else "readonly")

    # ---------- 按钮 / 进度 ----------
    def set_inspect_enabled(self, enabled):
        self.configure("inspect", state="normal" if enabled else "disabled")

    def set_running(self, running):
        self.configure("start", state="disabled" if running else "normal")
        self.configure("stop", state="normal" if running else "disabled")

    def set_progress(self, value):
        self.app.pb["value"] = value

    # ---------- 日志 ----------
    def append_log(self, lines):
        text = self.app.txt
        text.configure(state="normal")
        for line in (lines if isinstance(lines, (list, tuple)) else [lines]):
            text.insert("end", "[%s] %s\n" % (
                time.strftime("%m-%d %H:%M:%S"), line))
        n = int(text.index("end-1c").split(".")[0])
        if n > 4000:
            text.delete("1.0", "%d.0" % (n - 2000))
        text.see("end")
        text.configure(state="disabled")

    def clear_log(self):
        self.app.txt.configure(state="normal")
        self.app.txt.delete("1.0", "end")
        self.app.txt.configure(state="disabled")

    # ---------- 队列 ----------
    def set_queue(self, items, selected_id=None):
        items = list(items or [])
        selected = 0
        if selected_id is not None:
            for i, item in enumerate(items):
                if item.get("id") == selected_id:
                    selected = i
                    break
        self.app.lst_queue.set_items(items, selected)

    def queue_selection(self):
        return tuple(self.app.lst_queue.curselection())

    def queue_nearest(self, y):
        return self.app.lst_queue.nearest(y)

    # ---------- 帧段 ----------
    def set_segments(self, segments, focus=False):
        self.app.frames_module.set_segments(segments)
        if focus and self.app.frames_module.rows:
            self.app.frames_module.rows[-1]["start"].focus_set()

    def read_segments(self):
        return self.app.frames_module.read_segments()

    def segment_count(self):
        return len(self.app.frames_module.rows)

    def refresh_segments(self):
        self.app.frames_module.refresh_counts()

    # ---------- 折叠模块 ----------
    def set_module_expanded(self, key, expanded):
        card = self.app._modules[key]["card"]
        if bool(card.expanded) != bool(expanded):
            card.toggle_card()
        self.app._modules[key]["expanded"] = bool(expanded)

    def toggle_module(self, key):
        card = self.app._modules[key]["card"]
        card.toggle_card()
        self.app._modules[key]["expanded"] = card.expanded
        return card.expanded
