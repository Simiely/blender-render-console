# -*- coding: utf-8 -*-
"""界面与控制逻辑之间的**唯一契约**（不 import tkinter，可在单测里 Fake）。

`App` 以后只允许通过本契约读写控件；具体 Tk 实现在 `gui_view_new.TkView`。
换 UI 库时只需另写一个实现，不必再改控制器。契约故意用语义方法，
不把 `tk.StringVar` / `ttk.Combobox` 等类型漏进业务层。

变量清单是"控件背后的值"，回调清单是"控件触发的业务入口"；
两者都用名字注册，便于 `FakeView` 在单测中断言。
"""

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover - 仅兼容极老的解释器
    Protocol = object

    def runtime_checkable(cls):
        return cls


VARIABLES = (
    "blend", "scene", "blender", "output", "engine", "samples", "device",
    "width", "height", "pct", "format", "depth", "resume", "native",
    "retry", "autostart", "auto_resume", "aftermath", "blender_ver",
)

CALLBACKS = (
    "pick_blend", "on_queue_remove", "on_queue_move", "on_queue_reorder",
    "autofill_blender", "on_blender_version", "sync_retry_note",
    "on_toggle_autostart", "on_toggle_auto_resume", "on_queue_select",
    "on_start", "on_stop", "open_output_dir", "clear_state", "clear_log",
    "on_inspect", "on_scene_change", "maybe_inspect", "on_add_segment",
    "on_format_changed",
)


@runtime_checkable
class ViewProtocol(Protocol):
    """控制器依赖的全部视图能力。"""

    def var(self, name): ...

    def get(self, name): ...

    def set(self, name, value): ...

    def trace(self, name, mode, callback): ...

    def configure(self, name, **kw): ...

    def cget(self, name, key): ...

    def set_text(self, key, text, **kw): ...

    def text(self, key): ...

    def set_versions(self, values): ...

    def set_retry_values(self, values): ...

    def set_depth_values(self, values, disabled=False): ...

    def set_scene_values(self, values, disabled=False): ...

    def set_inspect_enabled(self, enabled): ...

    def set_running(self, running): ...

    def set_progress(self, value): ...

    def append_log(self, lines): ...

    def clear_log(self): ...

    def set_queue(self, items, selected_id=None): ...

    def queue_selection(self): ...

    def queue_nearest(self, y): ...

    def set_segments(self, segments, focus=False): ...

    def read_segments(self): ...

    def segment_count(self): ...

    def refresh_segments(self): ...

    def set_module_expanded(self, key, expanded): ...

    def toggle_module(self, key): ...


class FakeVar(object):
    """无 tkinter 的变量替身：支持 get/set/trace_add。"""

    def __init__(self, value=None):
        self._value = value
        self._traces = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for _mode, callback in list(self._traces):
            callback()

    def trace_add(self, mode, callback):
        self._traces.append((mode, callback))
        return len(self._traces) - 1


class FakeView(object):
    """契约的内存实现：供控制器单测断言"界面收到了什么"。"""

    def __init__(self):
        self.vars = {name: FakeVar() for name in VARIABLES}
        self.texts = {}
        self.calls = []
        self.queue_items = []
        self.queue_selected = ()
        self.segments = []
        self.log_lines = []
        self.progress_value = 0
        self.running = False
        self.inspect_enabled = True
        self.modules = {}
        self._segment_count = 0
        self._sequence = 0

    def _record(self, method, *args, **kw):
        self._sequence += 1
        self.calls.append((method, args, kw, self._sequence))

    def var(self, name):
        self._record("var", name)
        return self.vars[name]

    def get(self, name):
        self._record("get", name)
        return self.vars[name].get()

    def set(self, name, value):
        self._record("set", name, value)
        self.vars[name].set(value)

    def trace(self, name, mode, callback):
        self._record("trace", name, mode)
        return self.vars[name].trace_add(mode, callback)

    def configure(self, name, **kw):
        self._record("configure", name, **kw)

    def cget(self, name, key):
        self._record("cget", name, key)
        return ""

    def set_text(self, key, text, **kw):
        self._record("set_text", key, text, **kw)
        self.texts[key] = text

    def text(self, key):
        self._record("text", key)
        return self.texts.get(key, "")

    def set_versions(self, values):
        self._record("set_versions", values)
        self.configure("blender_ver", values=list(values))

    def set_retry_values(self, values):
        self._record("set_retry_values", values)
        self.configure("retry", values=list(values))

    def set_depth_values(self, values, disabled=False):
        self._record("set_depth_values", values, disabled)
        self.configure("depth", values=list(values),
                       state="disabled" if disabled else "readonly")

    def set_scene_values(self, values, disabled=False):
        self._record("set_scene_values", values, disabled)
        self.configure("scene", values=list(values),
                       state="disabled" if disabled else "readonly")

    def set_inspect_enabled(self, enabled):
        self._record("set_inspect_enabled", enabled)
        self.inspect_enabled = enabled

    def set_running(self, running):
        self._record("set_running", running)
        self.running = running

    def set_progress(self, value):
        self._record("set_progress", value)
        self.progress_value = value

    def append_log(self, lines):
        self._record("append_log", lines)
        for line in (lines if isinstance(lines, (list, tuple)) else [lines]):
            self.log_lines.append(line)

    def clear_log(self):
        self._record("clear_log")
        self.log_lines = []

    def set_queue(self, items, selected_id=None):
        self._record("set_queue", items, selected_id)
        self.queue_items = list(items or [])
        self.queue_selected = ()
        if selected_id is not None:
            for idx, item in enumerate(self.queue_items):
                if item.get("id") == selected_id:
                    self.queue_selected = (idx,)
                    break

    def queue_selection(self):
        self._record("queue_selection")
        return self.queue_selected

    def queue_nearest(self, y):
        self._record("queue_nearest", y)
        return max(0, min(len(self.queue_items) - 1, int(y) // 24))

    def set_segments(self, segments, focus=False):
        self._record("set_segments", segments, focus)
        self.segments = list(segments or [])
        self._segment_count = len(self.segments)

    def read_segments(self):
        self._record("read_segments")
        return list(self.segments)

    def segment_count(self):
        self._record("segment_count")
        return self._segment_count

    def refresh_segments(self):
        self._record("refresh_segments")
        self._segment_count = len(self.segments)

    def set_module_expanded(self, key, expanded):
        self._record("set_module_expanded", key, expanded)
        self.modules[key] = bool(expanded)

    def toggle_module(self, key):
        self._record("toggle_module", key)
        self.modules[key] = not self.modules.get(key, False)
        return self.modules[key]
