# -*- coding: utf-8 -*-
"""进度与日志——照 `tpl-log` 与 `.track/.statusline/.logbox` 做这一块。

track 9px 圆角；statusline 7px 圆点 + 文案；logbox 260px、8px 圆角；
顶栏右侧：断点提示 + 开始/停止/打开输出/清空断点/清空日志。
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit


class _ProgressProxy(object):
    def __init__(self, panel):
        self.panel = panel

    def __setitem__(self, key, value):
        if key == "value":
            self.panel.set_progress(value)


class _LabelProxy(object):
    def __init__(self, panel, kind):
        self.panel = panel
        self.kind = kind

    def configure(self, **kw):
        text = kw.get("text")
        if text is None:
            return
        if self.kind == "status":
            self.panel.status_var.set(text)
        else:
            self.panel.set_breakpoint(text)

    def cget(self, key):
        if key != "text":
            return ""
        return (self.panel.status_var.get() if self.kind == "status"
                else self.panel.breakpoint_var.get())


class LogPanel(uikit.ModuleCard):
    def __init__(self, master, on_start=None, on_stop=None, on_open=None,
                 on_clear_state=None, on_clear_log=None, demo=False, **kw):
        super().__init__(master, "进度与日志", icon="◉", summary="",
                         expanded=False, show_toggle=False, header_click=True,
                         **kw)
        self._callbacks = {
            "start": on_start or (lambda: None),
            "stop": on_stop or (lambda: None),
            "open": on_open or (lambda: None),
            "clear_state": on_clear_state or (lambda: None),
            "clear_log": on_clear_log or (lambda: None),
        }
        self._build_actions()
        body = self.body
        self.track = tk.Canvas(body, height=9, bg=theme.PANEL2,
                               highlightthickness=0, bd=0)
        self.track.pack(fill="x", pady=(2, 0))
        self.track.bind("<Configure>", lambda _e: self._draw_track())
        self.progress = 0

        status = tk.Frame(body, bg=theme.PANEL2)
        status.pack(fill="x", pady=(8, 2))
        dot = tk.Canvas(status, width=15, height=15, bg=theme.PANEL2,
                        highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 14, 14, fill=theme.ACCENT_SOFT, outline="")
        dot.create_oval(4, 4, 11, 11, fill=theme.ACCENT, outline="")
        dot.pack(side="left")
        self.status_var = tk.StringVar(value="空闲 · 尚未开始")
        tk.Label(status, textvariable=self.status_var, bg=theme.PANEL2,
                 fg="#d4d4d4", font=("Microsoft YaHei", 9)).pack(
                     side="left", padx=(8, 0))

        # 时间 / 速度 / ETA / 预计完成：单独一行，避免把状态行挤爆
        meta = tk.Frame(body, bg=theme.PANEL2)
        meta.pack(fill="x", pady=(0, 10))
        self.meta_var = tk.StringVar()
        self.set_metrics()
        tk.Label(meta, textvariable=self.meta_var, bg=theme.PANEL2,
                 fg=theme.MUTED, font=("Microsoft YaHei", 8)).pack(
                     anchor="w", padx=(23, 0))

        box = tk.Frame(body, bg=theme.LOG_BG, highlightthickness=1,
                       highlightbackground=theme.BORDER, height=260)
        box.pack(fill="both", expand=True)
        box.pack_propagate(False)
        self.text = tk.Text(box, wrap="word", font=("Consolas", 9),
                            relief="flat", bd=0, padx=0, pady=0,
                            **theme.log_widget_colors())
        self.text.pack(side="left", fill="both", expand=True,
                       padx=(11, 0), pady=9)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        sb.pack(side="right", fill="y", pady=6)
        self.text.configure(yscrollcommand=sb.set)
        self._tags()
        if demo:
            self._demo_log()
        self.txt = self.text
        self.pb = _ProgressProxy(self)
        self.lbl_status = _LabelProxy(self, "status")
        self.lbl_state = _LabelProxy(self, "state")
        self.btn_start = self.buttons["start"]
        self.btn_stop = self.buttons["stop"]
        self.btn_clear_state = self.buttons["clear_state"]

    def _build_actions(self):
        bar = tk.Frame(self.header, bg=theme.PANEL2)
        bar.pack(side="right", padx=(6, 6), pady=2)
        self.breakpoint_var = tk.StringVar(value="断点：无")
        tk.Label(bar, textvariable=self.breakpoint_var, bg=theme.PANEL2,
                 fg=theme.MUTED, font=("Microsoft YaHei", 8)).pack(
                     side="left", padx=(0, 6))
        self.buttons = {}
        specs = [("start", "▶ 开始渲染", "primary", True),
                 ("stop", "■ 停止", "secondary", False),
                 ("open", "↗ 打开输出目录", "secondary", True),
                 ("clear_state", "⌫ 清空断点", "secondary", True),
                 ("clear_log", "♲ 清空日志", "secondary", True)]
        for key, text, kind, enabled in specs:
            btn = uikit.RoundButton(bar, text=text, height=26, kind=kind,
                                    enabled=enabled,
                                    command=lambda k=key: self._fire(k))
            btn.pack(side="left", padx=(0, 5))
            self.buttons[key] = btn

    def _fire(self, key):
        if key == "start":
            self.expand()          # 点开始渲染＝现在就要看进度，自动把日志展开
        cb = self._callbacks.get(key)
        if cb:
            cb()

    def expand(self):
        if not self.expanded:
            self.toggle_card()

    def _draw_track(self):
        self.track.delete("all")
        w = max(self.track.winfo_width(), 10)
        h = 9
        r = h / 2.0
        self._pill(0, 0, w, h, r, theme.FIELD, outline=theme.BORDER)
        fw = max(0, int(w * self.progress / 100.0))
        if fw > 0:
            self._pill(0, 0, fw, h, r, theme.ACCENT, outline="")

    def _pill(self, x1, y1, x2, y2, r, fill, outline=""):
        pts = []
        corners = ((x2 - r, y1 + r, -90, 0), (x2 - r, y2 - r, 0, 90),
                   (x1 + r, y2 - r, 90, 180), (x1 + r, y1 + r, 180, 270))
        import math
        for cx, cy, a0, a1 in corners:
            for i in range(6):
                a = math.radians(a0 + (a1 - a0) * i / 5.0)
                pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
        self.track.create_polygon(pts, fill=fill, outline=outline)

    def _tags(self):
        self.text.tag_configure("ts", foreground="#6e6e6e")
        self.text.tag_configure("native", foreground="#8f9aa3")
        self.text.tag_configure("ok", foreground=theme.OK)
        self.text.tag_configure("warn", foreground=theme.WARN)
        self.text.tag_configure("accent", foreground=theme.ACCENT)

    def _demo_log(self):
        self.text.configure(state="normal")
        rows = [
            ("10:42:01", "已启动 Blender", "accent"),
            ("10:42:02", "Fra:42 Mem:812MB 渲染中", "native"),
            ("10:42:03", "Fra:42 已保存 frame_0042.png", "ok"),
        ]
        for ts, msg, tag in rows:
            self.text.insert("end", ts + "  ", "ts")
            self.text.insert("end", msg + "\n", tag)
        self.text.configure(state="disabled")

    def set_metrics(self, elapsed="—", per_frame="—", speed="—", eta="—",
                    finish="—"):
        """时间 / 单帧 / 速度 / ETA / 预计完成，由业务层填格式化后的文案。"""
        self.meta_var.set(
            "用时 %s　·　单帧 %s　·　速度 %s　·　ETA %s　·　预计 %s 完成"
            % (elapsed, per_frame, speed, eta, finish))

    def set_breakpoint(self, text):
        self.breakpoint_var.set(text or "断点：无")

    def append(self, text, tag=None):
        """业务日志入口：自动滚到底，保留 Text 只读。"""
        self.text.configure(state="normal")
        self.text.insert("end", str(text) + "\n", tag or ())
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear_log(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def set_progress(self, pct, text=None):
        self.progress = max(0, min(100, int(pct)))
        if text is not None:
            self.status_var.set(text)
        self._draw_track()
