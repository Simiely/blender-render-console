# -*- coding: utf-8 -*-
"""新界面装配层：顶栏 + 左栏 + 右侧模块 + 日志，全部用确认版零件。

`gui.py` 仍只做业务；这里负责把业务变量/回调注入模块，并暴露旧控件别名，
让已有业务方法不用整体重写。
"""

import time
import tkinter as tk

from . import theme, uikit
from .guimodel import (AFTERMATH_LABELS, DEFAULT_RETRY, DEPTH_KEEP_LABEL,
                       DEVICES, ENGINES, FORMATS,
                       SCENE_NEED_READ_LABEL, format_label, retry_note)
from .ui_frames import FramesModule
from .ui_leftqueue import LeftQueuePanel
from .ui_log import LogPanel
from .ui_overview import OverviewModule
from .ui_project import ProjectModule
from .ui_render import RenderModule


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
    app.left = left
    left.bind("<<ListboxSelect>>", app._on_queue_select)

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

    scroll = uikit.ScrollArea(right)
    scroll.pack(fill="both", expand=True)
    content = scroll.inner

    overview = _stack(content, OverviewModule(content), first=True)
    app.overview = overview

    project = _stack(content, ProjectModule(
        content, on_browse=app.pick_blend, on_inspect=app.on_inspect,
        on_scene_change=app.on_scene_change, on_blend_commit=app.maybe_inspect,
        blend_var=app.v_blend, scene_var=app.v_scene))
    app.project = project

    frames = _stack(content, FramesModule(content, on_add=app.on_add_segment))
    app.frames_module = frames

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

    prog = left.program
    app.program = prog
    app.v_blender = prog.vars["blender"]
    app.v_blender_ver = prog.vars["blender_ver"]
    app.v_native = prog.vars["native"]
    app.v_retry = prog.vars["retry"]
    app.v_autostart = prog.vars["autostart"]
    app.v_auto_resume = prog.vars["auto_resume"]
    app.v_aftermath = prog.vars["aftermath"]

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

class TkView(object):
    """`brconsole.view_contract.ViewProtocol` 的 Tk 实现。

    控制器只通过语义方法访问界面；`build()` 里不再挂旧控件别名。
    """

    def __init__(self, app, root):
        self.app = app
        self.root = root
        self._texts = {}
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
    def _control(self, name):
        return {
            "depth": self.app.render.depth_combo,
            "scene": self.app.project.scene_combo,
            "inspect": self.app.project.actions["inspect"],
            "start": self.app.log_panel.btn_start,
            "stop": self.app.log_panel.btn_stop,
            "clear_state": self.app.log_panel.btn_clear_state,
        }[name]

    def configure(self, name, **kw):
        if name == "blender_ver":
            if "values" in kw:
                self.app.program.set_versions(kw["values"])
            if "width" in kw:
                self.app.program.set_version_width(kw["width"])
            return
        if name == "retry":
            if "values" in kw:
                self.app.program.set_retry_values(kw["values"])
            if "width" in kw:
                self.app.program.set_retry_width(kw["width"])
            return
        if name == "retry_note":
            self.app.program.set_retry_note(kw.get("text"))
            return
        if name == "autostart_note":
            self.app.program.set_autostart_note(kw.get("text"))
            return
        self._control(name).configure(**kw)

    def cget(self, name, key):
        return self._control(name).cget(key)

    def set_text(self, key, text, **kw):
        if key == "status":
            self.app.log_panel.status_var.set(text)
        elif key == "state":
            self.app.log_panel.set_breakpoint(text)
        elif key == "collection_status":
            self.app.project.collection_status.set(text)
        elif key == "inspect_note":
            self.app.project.inspect_status.set(text)
        elif key == "retry_note":
            self.app.program.set_retry_note(text)
        elif key == "autostart_note":
            self.app.program.set_autostart_note(text)
        elif key == "queue_count":
            self.app.left.count.configure(text=text)
        else:
            self._texts[key] = text

    def text(self, key):
        if key == "status":
            return self.app.log_panel.status_var.get()
        if key == "state":
            return self.app.log_panel.breakpoint_var.get()
        if key == "collection_status":
            return self.app.project.collection_status.get()
        if key == "inspect_note":
            return self.app.project.inspect_status.get()
        if key == "queue_count":
            return self.app.left.count.cget("text")
        return self._texts.get(key, "")

    def set_disabled(self, key, disabled):
        self.app.program.set_disabled(key, disabled)

    def set_overview(self, key, value):
        field = self.app.overview.fields["range" if key == "frames" else key]
        field.set(str(value))

    def set_overview_head(self, name, status="pending"):
        self.app.overview.set_head(name, status)

    def set_summary(self, key, text):
        self._texts["summary." + key] = text

    def rebuild_collections(self, collections, hidden=(), on_toggle=None):
        self.app.project.set_collections(collections, hidden, on_toggle=on_toggle)

    def collection_hidden(self):
        return self.app.project.hidden_collections()

    def expand_log(self):
        self.app.log_panel.expand()

    def set_clear_state_enabled(self, enabled):
        self.configure("clear_state", state="normal" if enabled else "disabled")

    def set_metrics(self, elapsed, per_frame, speed, eta, finish):
        self.app.log_panel.set_metrics(elapsed, per_frame, speed, eta, finish)

    # ---------- 下拉 ----------
    def set_versions(self, values):
        self.app.program.set_versions(values)

    def set_retry_values(self, values):
        self.app.program.set_retry_values(values)

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
        self.app.log_panel.set_progress(value)

    # ---------- 日志 ----------
    def append_log(self, lines):
        text = self.app.log_panel.text
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
        text = self.app.log_panel.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")

    # ---------- 队列 ----------
    def set_queue(self, items, selected_id=None):
        items = list(items or [])
        selected = 0
        if selected_id is not None:
            for i, item in enumerate(items):
                if item.get("id") == selected_id:
                    selected = i
                    break
        self.app.left.set_items(items, selected)

    def queue_selection(self):
        return tuple(self.app.left.curselection())

    def queue_nearest(self, y):
        return self.app.left.nearest(y)

    def clear_queue(self):
        self.app.left.delete(0, "end")

    def add_queue_item(self, text, status="pending"):
        self.app.left.insert("end", text)
        if status == "done":
            self.app.left.itemconfig("end", foreground=theme.MUTED)
        elif status == "failed":
            self.app.left.itemconfig("end", foreground=theme.WARN)

    def set_queue_count(self, text):
        self.app.left.count.configure(text=text)

    def set_queue_summary(self, text):
        self._texts["queue"] = text

    def select_queue(self, index):
        self.app.left.selection_clear(0, "end")
        self.app.left.selection_set(index)
        self.app.left.see(index)

    # ---------- 帧段 ----------
    def set_segments(self, segments, focus=False):
        self.app.frames_module.set_segments(segments)
        if focus and self.app.frames_module.rows:
            self.app.frames_module.rows[-1]["start"].focus_set()

    def add_segment(self, start="", end="", step="1", focus=False):
        return self.app.frames_module.add_segment(start, end, step, focus=focus)

    def remove_segment(self, handle):
        self.app.frames_module.remove_handle(handle)

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
