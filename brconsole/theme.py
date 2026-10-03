# -*- coding: utf-8 -*-
"""深色主题。

tkinter 默认的 ttk 主题（Windows 上是 `vista`）**不接受颜色配置**，改背景色一律无效；
所以这里强制切到 `clam`（可完全自定义），再逐类控件配深色。

配色取自 VS Code 深色：
    背景 #1e1e1e · 面板 #252526 · 输入框/边框 #3c3c3c
    正文 #d4d4d4 · 次要文字 #9aa0a6 · 强调蓝 #0e639c / #4a9eff
"""

BG = "#1e1e1e"
PANEL = "#252526"
FIELD = "#3c3c3c"
BORDER = "#454545"
FG = "#d4d4d4"
MUTED = "#9aa0a6"
ACCENT = "#0e639c"
ACCENT_HOVER = "#1177bb"
OK = "#4ec9b0"

# 日志区（等宽字体，比正文更暗一点）
LOG_BG = "#181818"
LOG_FG = "#cfcfcf"


def apply(root):
    """给整个 Tk 根窗口套上深色主题，返回 ttk.Style 实例。"""
    from tkinter import ttk

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(background=BG)

    # 全局兜底：没单独配的控件也不会亮瞎眼
    style.configure(".", background=PANEL, foreground=FG,
                    fieldbackground=FIELD, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL, troughcolor=FIELD,
                    selectbackground=ACCENT, selectforeground="#ffffff",
                    focuscolor=ACCENT)

    style.configure("TFrame", background=PANEL)
    style.configure("TLabel", background=PANEL, foreground=FG)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Accent.TLabel", background=PANEL, foreground="#8ab4f8")
    style.configure("OK.TLabel", background=PANEL, foreground=OK)

    style.configure("TLabelframe", background=PANEL, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=PANEL, foreground="#9cdcfe")

    style.configure("TButton", background=FIELD, foreground=FG,
                    bordercolor=BORDER, relief="flat", padding=(10, 4))
    style.map("TButton",
              background=[("pressed", "#2a2d2e"), ("active", "#4a4a4a"),
                          ("disabled", "#2d2d2d")],
              foreground=[("disabled", "#6e6e6e")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
    style.map("Accent.TButton",
              background=[("pressed", "#0a4d78"), ("active", ACCENT_HOVER),
                          ("disabled", "#2d2d2d")])

    style.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                    insertcolor=FG, bordercolor=BORDER, relief="flat")
    style.map("TEntry", fieldbackground=[("disabled", "#2d2d2d")],
              foreground=[("disabled", "#6e6e6e")])

    style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                    foreground=FG, arrowcolor=FG, bordercolor=BORDER,
                    selectbackground=FIELD, selectforeground=FG)
    style.map("TCombobox",
              fieldbackground=[("readonly", FIELD), ("disabled", "#2d2d2d")],
              foreground=[("readonly", FG), ("disabled", "#6e6e6e")])
    # 下拉列表是 Tk 的 Listbox，不走 ttk 样式，只能走 option 数据库
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    style.configure("TCheckbutton", background=PANEL, foreground=FG,
                    indicatorcolor=FIELD)
    style.map("TCheckbutton",
              background=[("active", PANEL)],
              indicatorcolor=[("selected", ACCENT), ("active", "#4a4a4a")])

    style.configure("TProgressbar", background=ACCENT, troughcolor=FIELD,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

    style.configure("TScrollbar", background=FIELD, troughcolor=PANEL,
                    bordercolor=PANEL, arrowcolor=FG)
    style.map("TScrollbar", background=[("active", "#4a4a4a")])

    style.configure("TSeparator", background=BORDER)
    style.configure("TRadiobutton", background=PANEL, foreground=FG)

    return style


def log_widget_colors():
    """日志 Text 控件（Tk 原生，不走 ttk）的配色。"""
    return {"background": LOG_BG, "foreground": LOG_FG,
            "insertbackground": FG, "selectbackground": ACCENT}
