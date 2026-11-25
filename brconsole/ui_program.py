# -*- coding: utf-8 -*-
"""左栏「程序设置」入口 + 独立弹窗。

左栏只保留一行可点击标题；点击后弹出模态设置窗，字段与 `tpl-program` 一致：
Blender 程序/版本、原生输出、失败重试、开机自启、启动续跑、任务结束后。
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit
from .guimodel import (AFTERMATH_LABELS, DEFAULT_RETRY, RETRY_CHOICES,
                       retry_note)


class ProgramDialog(tk.Toplevel):
    def __init__(self, master, owner):
        super().__init__(master)
        self.owner = owner
        self.title("程序设置")
        self.configure(bg=theme.PANEL)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

        body = tk.Frame(self, bg=theme.PANEL)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        body.columnconfigure(1, weight=1)
        v = owner.vars

        # Blender 程序
        ttk.Label(body, text="Blender 程序", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        self.blender_entry = ttk.Entry(body, textvariable=v["blender"])
        self.blender_entry.grid(row=1, column=0, columnspan=2, sticky="ew",
                                pady=(0, 6))
        uikit.RoundButton(body, text="自动探测", height=26,
                          command=owner.on_detect or (lambda: None)).grid(
                              row=2, column=0, sticky="w", pady=(0, 10))

        # Blender 版本
        ttk.Label(body, text="Blender 版本", style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", pady=(0, 4))
        self.version_combo = ttk.Combobox(
            body, textvariable=v["blender_ver"], state="readonly",
            values=list(owner.version_values))
        if owner.version_width is not None:
            self.version_combo.configure(width=owner.version_width)
        self.version_combo.grid(row=4, column=0, columnspan=2, sticky="ew")
        if owner.on_version:
            self.version_combo.bind("<<ComboboxSelected>>", owner.on_version)
        ttk.Label(body, text="探测到多个版本时可切换",
                  style="Muted.TLabel").grid(row=5, column=0, sticky="w",
                                             pady=(3, 10))

        # 原生输出
        self.native_check = ttk.Checkbutton(
            body, text="显示 Blender 原生输出（同类进度行自动折叠）",
            variable=v["native"], style="TCheckbutton",
            anchor="w", justify="left", wraplength=480)
        self.native_check.grid(row=6, column=0, columnspan=2, sticky="w",
                               pady=(0, 10))

        # 失败重试
        ttk.Label(body, text="失败重试", style="Muted.TLabel").grid(
            row=7, column=0, sticky="w", pady=(0, 4))
        self.retry_combo = ttk.Combobox(
            body, textvariable=v["retry"], state="readonly",
            values=list(owner.retry_values))
        if owner.retry_width is not None:
            self.retry_combo.configure(width=owner.retry_width)
        self.retry_combo.grid(row=8, column=0, columnspan=2, sticky="ew")
        if owner.on_retry:
            self.retry_combo.bind("<<ComboboxSelected>>", owner.on_retry)
        self.retry_note = ttk.Label(
            body, text=retry_note(v["retry"].get()), style="Muted.TLabel",
            wraplength=500, justify="left")
        self.retry_note.grid(row=9, column=0, columnspan=2, sticky="w",
                             pady=(4, 10))

        # 开机自启 / 自动续跑
        self.autostart_check = ttk.Checkbutton(
            body, text="开机自动启动", variable=v["autostart"],
            command=owner.on_autostart, style="TCheckbutton", anchor="w")
        self.autostart_check.grid(row=10, column=0, columnspan=2, sticky="w")
        self.auto_resume_check = ttk.Checkbutton(
            body, text="启动时自动续跑未完成任务",
            variable=v["auto_resume"], command=owner.on_auto_resume,
            style="TCheckbutton", anchor="w")
        self.auto_resume_check.grid(row=11, column=0, columnspan=2,
                                    sticky="w", pady=(2, 0))
        self.autostart_note = ttk.Label(
            body, text="", style="Muted.TLabel", wraplength=500,
            justify="left")
        self.autostart_note.grid(row=12, column=0, columnspan=2,
                                 sticky="w", pady=(3, 10))

        # 任务结束后
        ttk.Label(body, text="任务结束后", style="Muted.TLabel").grid(
            row=13, column=0, sticky="w", pady=(0, 4))
        self.aftermath_combo = ttk.Combobox(
            body, textvariable=v["aftermath"], state="readonly",
            values=[label for label, _ in AFTERMATH_LABELS])
        self.aftermath_combo.grid(row=14, column=0, columnspan=2, sticky="ew",
                                  pady=(0, 14))

        foot = tk.Frame(body, bg=theme.PANEL)
        foot.grid(row=15, column=0, columnspan=2, sticky="e")
        uikit.RoundButton(foot, text="关闭", height=28, width=76,
                          command=self.close).pack(side="right")

        # 业务层在建窗之前就可能把某个勾选框置灰（例如系统不支持开机自启）
        for key in getattr(owner, "disabled", ()):
            self.apply_disabled(key, True)

        self.update_idletasks()
        w, h = 540, max(420, self.winfo_reqheight())
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + 60
        self.geometry("%dx%d+%d+%d" % (w, h, max(x, 0), max(y, 0)))
        self.grab_set()
        self.blender_entry.focus_set()

    def refresh_versions(self, versions):
        self.version_combo.configure(values=list(versions))

    def refresh_version_width(self, width):
        self.version_combo.configure(width=width)

    def refresh_retry_values(self, values):
        self.retry_combo.configure(values=list(values))

    def refresh_retry_width(self, width):
        self.retry_combo.configure(width=width)

    def refresh_retry_note(self, text):
        self.retry_note.configure(text=text)

    def refresh_autostart_note(self, text):
        self.autostart_note.configure(text=text or "")

    def apply_disabled(self, key, flag=True):
        widget = {"autostart": self.autostart_check,
                  "auto_resume": self.auto_resume_check}.get(key)
        if widget is not None:
            widget.configure(state="disabled" if flag else "normal")

    def close(self):
        self.owner.dialog = None
        try:
            self.owner.title.configure(fg=theme.MUTED)
        except tk.TclError:
            pass
        self.grab_release()
        self.destroy()


class ProgramSettingsPanel(tk.Frame):
    """左栏入口：只有一行标题，点击弹窗。"""

    def __init__(self, master, on_detect=None, on_version=None, on_retry=None,
                 on_autostart=None, on_auto_resume=None, **kw):
        super().__init__(master, bg=theme.NAV_BG, **kw)
        self.on_detect = on_detect
        self.on_version = on_version
        self.on_retry = on_retry
        self.on_autostart = on_autostart
        self.on_auto_resume = on_auto_resume
        self.dialog = None
        self.version_values = []
        self.version_width = None
        self.retry_values = list(RETRY_CHOICES)
        self.retry_width = None
        self.disabled = set()
        self.retry_note_text = retry_note(DEFAULT_RETRY)
        self.autostart_note_text = ""
        self.vars = {
            "blender": tk.StringVar(),
            "blender_ver": tk.StringVar(),
            "native": tk.BooleanVar(value=True),
            "retry": tk.StringVar(value=DEFAULT_RETRY),
            "autostart": tk.BooleanVar(value=False),
            "auto_resume": tk.BooleanVar(value=True),
            "aftermath": tk.StringVar(value=AFTERMATH_LABELS[0][0]),
        }
        self.head = tk.Frame(self, bg=theme.NAV_BG, cursor="hand2")
        self.head.pack(fill="x")
        self.title = tk.Label(self.head, text="⚙ 程序设置", bg=theme.NAV_BG,
                              fg=theme.MUTED,
                              font=("Microsoft YaHei", 9, "bold"))
        self.title.pack(side="left", padx=12, pady=10)
        for w in (self.head, self.title):
            w.bind("<Button-1>", self.open_dialog)

    def open_dialog(self, _event=None):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            self.dialog.focus_force()
            return
        self.title.configure(fg=theme.ACCENT)
        self.dialog = ProgramDialog(self.winfo_toplevel(), self)

    def set_versions(self, versions, current=None):
        self.version_values = list(versions)
        if current is not None:
            self.vars["blender_ver"].set(current)
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.refresh_versions(versions)

    def set_version_width(self, width):
        self.version_width = width
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.refresh_version_width(width)

    def set_retry_values(self, values):
        self.retry_values = list(values)
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.refresh_retry_values(values)

    def set_retry_width(self, width):
        self.retry_width = width
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.refresh_retry_width(width)

    def set_retry_note(self, text=None):
        text = text or retry_note(self.vars["retry"].get())
        self.retry_note_text = text
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.refresh_retry_note(text)

    def set_autostart_note(self, text):
        self.autostart_note_text = text or ""
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.refresh_autostart_note(text)

    def set_disabled(self, key, flag=True):
        """把某个勾选框置灰/恢复（弹窗还没开就先记着，开的时候再应用）。"""
        if flag:
            self.disabled.add(key)
        else:
            self.disabled.discard(key)
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.apply_disabled(key, flag)
