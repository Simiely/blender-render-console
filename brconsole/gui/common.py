# -*- coding: utf-8 -*-
"""GUI 包的极小共享层：常量、时间戳、图标与下拉宽度工具。

这里**不再**集中 re-export 业务模块；每个 controller 直接 import 自己
真正需要的东西，避免 common 变成新的高扇入瓶颈。
"""

import os
import sys
import time
from tkinter import font as tkfont

from .. import __version__
from ..layout import FROZEN_SUBDIR
from ..guimodel.constants import (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN,
                                  AFTERMATH_SLEEP)

POLL_MS = 80
LOG_MAX_LINES = 4000
TITLE = "blender-render-console"
#: 各模块内部 grid 的统一间距（模块级常量：拆出来的 _build_* 方法也要用）
PAD = {"padx": 6, "pady": 4}
#: 标题栏带版本号：用户经常同时装着几个版本，"这是哪个版本"必须能一眼看到。
#: ⚠️ 版本号**只从** `brconsole.__version__` 取，别在这里再写一份字面量。
WINDOW_TITLE = "%s %s · 无头渲染控制台" % (TITLE, __version__)

# 「任务结束后」下拉：显示文案 ↔ 实际值
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
    """设窗口/任务栏图标；图标缺失或格式不对不该影响功能。"""
    try:
        p = icon_path()
        if os.path.exists(p):
            root.iconbitmap(default=p)
    except Exception:
        pass


def fit_combobox(cb, values=None, extra=1):
    """按字体实测算下拉框的 `-width`（字符数），让最长的选项能完整显示。

    ttk 的 `-width` 单位是字符数，中文是双宽；按 `Font.measure()` 折算一次，
    换 DPI / 换字号都不会再截断。探测失败不挡住建界面。
    """
    try:
        f = tkfont.Font(root=cb, font=cb.cget("font") or "TkDefaultFont")
        unit = max(1, f.measure("0"))
        widest = max(f.measure(str(v)) for v in (values or cb.cget("values")))
        cb.configure(width=int(round(widest / float(unit))) + extra)
    except Exception:
        pass
