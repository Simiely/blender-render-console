# -*- coding: utf-8 -*-
"""GUI 包共用的导入、常量与小工具。

这些名字原本都在 `gui.py` 顶部；拆包后只保留一份，控制器从此处复用。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .. import (applock, autostart, diskspace, locate, notify, power,
               queue_store, taskstore, theme, uikit)
from .. import __version__
from ..layout import FROZEN_SUBDIR
from ..core import JobConfig, RenderJob
from ..guimodel import (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN, AFTERMATH_SLEEP,
                       DEFAULT_RETRY, DEPTH_KEEP_LABEL, DEVICES, ENGINES, FORMATS,
                       RETRY_CHOICES, SCENE_NEED_READ_LABEL,
                       checks_from_hidden, coerce_depth, depth_choices, depth_value,
                       format_ext_for, format_from_label, format_label,
                       hidden_from_checks, retarget_extension,
                       retry_note,
                       SCENE_PLACEHOLDERS, FormModel, LogModel, NativeLineFilter,
                       ProgressModel,
                       aftermath_plan, event_line, fields_from_detail,
                       fmt_clock, fmt_duration,
                       frames_preview, frames_to_segments, guess_state_path,
                       new_segment, normalise_segment, segment_label,
                       segments_to_frames,
                       scene_to_config, settle_task_action,
                       state_summary)
from ..inspect import (read_blend_info, scene_detail, scene_names,
                      summarize as summarize_blend)

POLL_MS = 80
LOG_MAX_LINES = 4000
TITLE = "blender-render-console"
#: 各模块内部 grid 的统一间距（模块级常量：拆出来的 _build_* 方法也要用）
PAD = {"padx": 6, "pady": 4}
#: 标题栏带版本号：用户经常同时装着几个版本，"这是哪个版本"必须能一眼看到。
#: ⚠️ 版本号**只从** `brconsole.__version__` 取，别在这里再写一份字面量。
#: 标题变了就得同步 `applock.focus_window()` 的查找方式（它按前缀兜底，见那里）。
WINDOW_TITLE = "%s %s · 无头渲染控制台" % (TITLE, __version__)

AUTOSTART_FLAG = autostart.AUTOSTART_FLAG

# 「任务结束后」下拉：显示文案 ↔ 实际值（值就是 guimodel.AFTERMATH_*，别再写一份字面量）
AFTERMATH_LABELS = [("什么都不做", AFTERMATH_NOTHING),
                    ("关机（60 秒倒计时，可取消）", AFTERMATH_SHUTDOWN),
                    ("睡眠", AFTERMATH_SLEEP)]
AFTERMATH_BY_LABEL = {label: value for label, value in AFTERMATH_LABELS}


def _stamp():
    # 带日期：过夜渲染跨天后，"01:23:45"分不清是今天还是昨天
    return time.strftime("%m-%d %H:%M:%S")


def icon_path():
    """app.ico 的位置：源码运行在仓库 `assets/` 下，打包后在 `_brc/assets/` 下。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        return os.path.join(base, FROZEN_SUBDIR, "assets", "app.ico")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "app.ico")


def _apply_icon(root):
    """设窗口/任务栏图标。

    exe 自己的图标由打包时写进 PE 资源，但 **Tk 窗口不会自动继承** —— 不设的话
    任务栏和标题栏会显示 Tk 自带的羽毛图标。用 `default=` 让后续弹出的子窗口也带上。
    图标缺失或格式不对不该影响功能，所以整体吞异常。
    """
    try:
        p = icon_path()
        if os.path.exists(p):
            root.iconbitmap(default=p)
    except Exception:
        pass


def fit_combobox(cb, values=None, extra=1):
    """按字体实测算下拉框的 `-width`（字符数），让最长的选项能完整显示。

    为什么要算：ttk 的 `-width` 单位是**字符数**，而它的"字符宽"取自字体的平均
    字符宽 —— 中文是双宽，所以 `width=22` 装不下「一直重启，直到全部渲完（默认）」
    这种 15 个汉字的文案，下拉框里显示成尾部被硬切的半句、连省略号都没有
    （Tk 的 combobox 不做 ellipsis）。
    按 `Font.measure()` 折算一次，换 DPI / 换字号都不会再截断。
    """
    try:
        f = tkfont.Font(root=cb, font=cb.cget("font") or "TkDefaultFont")
        unit = max(1, f.measure("0"))
        widest = max(f.measure(str(v)) for v in (values or cb.cget("values")))
        cb.configure(width=int(round(widest / float(unit))) + extra)
    except Exception:
        pass                                    # 字号/字体探测失败不该挡住建界面


