# -*- coding: utf-8 -*-
"""顶栏——只照 `.titlebar` 做这一块。

height 44px；logo 22×22（沿用程序图标）；背景 #2a2a2f → #222226 竖向渐变；
底边 1px #454550。**不再重复程序名/版本**——窗口标题栏已经写着了。
"""

import math
import sys
import tkinter as tk
from pathlib import Path

from . import theme
from .layout import FROZEN_SUBDIR

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "app.png"


def icon_candidates():
    """logo 图的位置：源码在仓库 `assets/`，打包后在 `_brc/assets/`。"""
    paths = [ICON_PATH]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        paths.insert(0, Path(base) / FROZEN_SUBDIR / "assets" / "app.png")
        paths.append(Path(base) / "assets" / "app.png")
    return [str(p) for p in paths if Path(p).exists()]


class TopBar(tk.Canvas):
    HEIGHT = 44

    def __init__(self, master, title="blender-render-console", **kw):
        super().__init__(master, height=self.HEIGHT, highlightthickness=0,
                         bd=0, bg=theme.PANEL, **kw)
        self.title = title
        self._items = {}
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        self.delete("all")
        w = max(self.winfo_width(), 200)
        h = self.HEIGHT

        # 渐变背景：44 条 1px 线。终点刻意落到面板色（#252526），
        # 这样顶栏和下面的内容区之间是无缝的，不会留一条暗带/亮线。
        top = (0x2a, 0x2a, 0x2f)
        bottom = (0x25, 0x25, 0x26)
        for y in range(h):
            t = y / float(h - 1)
            rgb = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            self.create_line(0, y, w, y, fill="#%02x%02x%02x" % rgb)

        # 品牌行只留程序图标：程序名与版本已在窗口标题栏里，界面里再说一遍就是重复
        try:
            if not hasattr(self, "_logo_img"):
                found = icon_candidates()
                if not found:
                    raise IOError("logo 缺失")
                img = tk.PhotoImage(file=found[0])
                if img.width() > 22:
                    img = img.subsample(2)
                self._logo_img = img
            self.create_image(25, 22, image=self._logo_img, anchor="center")
        except Exception:
            self._round_rect(14, 11, 36, 33, 6, theme.ACCENT)
            self.create_text(25, 22, text="B", fill=theme.ON_ACCENT,
                             font=("Segoe UI", 10, "bold"))

    def _round_rect(self, x1, y1, x2, y2, r, fill):
        # 8 个圆角各取 6 个点，smooth=False 才是真正的圆角矩形（不是圆/菱形）
        pts = []
        corners = (
            (x2 - r, y1 + r, -90, 0), (x2 - r, y2 - r, 0, 90),
            (x1 + r, y2 - r, 90, 180), (x1 + r, y1 + r, 180, 270),
        )
        for cx, cy, a0, a1 in corners:
            for i in range(6):
                a = math.radians(a0 + (a1 - a0) * i / 5.0)
                pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
        self.create_polygon(pts, fill=fill, outline="")
