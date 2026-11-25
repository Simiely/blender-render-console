# -*- coding: utf-8 -*-
"""「当前队列项」概览——照 `.qdetail` / `qd-head` / `qd-grid` 做这一个模块。

10 个字段按 auto-fill minmax(178px,1fr) 排；标签 11px muted，值 #e8e8e8 粗体；
外框是 accent 38% 边框 + 淡粉底。
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit

QD_BG = "#332a2d"          # rgba(255,146,146,.08) 落在 #2b2b30 上的近似
QD_BORDER = "#7a4a4a"


class OverviewModule(uikit.ModuleCard):
    def __init__(self, master, **kw):
        super().__init__(master, "当前队列项", icon="▤", summary="",
                         expanded=True, show_toggle=False, **kw)
        self.fields = {}
        self.qd = tk.Frame(self.body, bg=QD_BG, highlightthickness=1,
                           highlightbackground=QD_BORDER)
        self.qd.pack(fill="x", pady=(6, 2))

        head = tk.Frame(self.qd, bg=QD_BG)
        head.pack(fill="x", padx=12, pady=(10, 8))
        self.name_var = tk.StringVar(value="未选工程")
        tk.Label(head, textvariable=self.name_var, bg=QD_BG, fg="#ffffff",
                 font=("Microsoft YaHei", 10, "bold")).pack(side="left")
        self.pill = uikit.StatusPill(head, text="待跑", status="pending")
        self.pill.pack(side="left", padx=(8, 0))
        self.head = head

        grid = tk.Frame(self.qd, bg=QD_BG)
        grid.pack(fill="x", padx=12, pady=(0, 10))
        for c in range(4):
            grid.columnconfigure(c, weight=1, uniform="qd", minsize=178)
        rows = [("工程", "blend"), ("帧范围", "range"), ("帧数", "count"),
                ("引擎 / 采样", "engine"), ("输出", "output"),
                ("分辨率", "res"), ("格式 / 位数", "format"),
                ("设备", "device"), ("渲染时隐藏", "hidden"),
                ("重试 / 断点", "retry")]
        for i, (caption, key) in enumerate(rows):
            cell = tk.Frame(grid, bg=QD_BG)
            cell.grid(row=i // 4, column=i % 4, sticky="nw",
                      padx=(0, 12), pady=(0, 7))
            tk.Label(cell, text=caption, bg=QD_BG, fg=theme.MUTED,
                     font=("Microsoft YaHei", 8)).pack(anchor="w")
            var = tk.StringVar(value="—")
            tk.Label(cell, textvariable=var, bg=QD_BG, fg=theme.BRIGHT,
                     font=("Microsoft YaHei", 9, "bold"), justify="left",
                     wraplength=178, anchor="w").pack(anchor="w")
            self.fields[key] = var

    def set_head(self, name, status="pending"):
        """标题行：工程名 + 状态胶囊（业务层每次刷新队列时同步）。"""
        self.name_var.set(name or "未选工程")
        self.pill.set_status(status, uikit.StatusPill.LABELS.get(status, status))

    @staticmethod
    def _short_retry(value):
        """概览里只放短文案：「一直重启，直到全部渲完」→「一直重试」。"""
        text = str(value or "—")
        retry, sep, tail = text.partition(" · ")
        if "一直" in retry:
            retry = "一直重试"
        elif "不" in retry and "重试" in retry:
            retry = "不重试"
        elif "重试" in retry and len(retry) > 6:
            retry = "重试"
        return retry + (sep + tail if sep else "")

    def set_item(self, item):
        self.name_var.set(item.get("name", "—"))
        status = item.get("status", "pending")
        self.pill.set_status(status, uikit.StatusPill.LABELS.get(status, status))
        for key, var in self.fields.items():
            value = item.get(key, "—")
            if key == "retry":
                value = self._short_retry(value)
            var.set(str(value))
