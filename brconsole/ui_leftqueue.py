# -*- coding: utf-8 -*-
"""左栏队列——照 `.navcol/.qnav-head/.qnav-item/.qnav-actions` 做这一块。

208px 宽、#202024 底、右侧 1px 边框；队列项是 8px 圆角卡片，
选中时 accent-soft 底 + accent 边框 + 左侧 3px 粉条；底部操作条 + 折叠程序设置。
"""

import tkinter as tk
from tkinter import ttk

from . import theme, uikit
from .ui_program import ProgramSettingsPanel


CARD_BG = "#2b2b30"
SEL_BG = theme.ACCENT_SOFT


class QNavItem(tk.Frame):
    """队列卡片：8px 圆角 + 1px 边框，选中时淡粉底 + 左侧 3px 粉条。

    圆角用 Pillow 位图铺底（和按钮同一套抗锯齿），四个角透明、露出左栏底色；
    内容放在内缩 8px 的 Frame 里，方角落在圆角内部，不会啃掉圆角。
    """

    HEIGHT = 64
    RADIUS = 8
    PAD = 8

    def __init__(self, master, name, meta, status, selected=False):
        super().__init__(master, bg=theme.NAV_BG, height=self.HEIGHT,
                         highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self.selected = selected
        self._art = tk.Label(self, bd=0, highlightthickness=0, bg=theme.NAV_BG)
        self._art.place(x=0, y=0, relwidth=1, relheight=1)
        self._photo = None

        fill = SEL_BG if selected else CARD_BG
        self.body = tk.Frame(self, bg=fill)
        self.body.place(x=13, rely=0.5, relwidth=1, width=-26, anchor="w")
        self.top = tk.Frame(self.body, bg=fill)
        self.top.pack(fill="x")
        self.name = tk.Label(self.top, text=name, bg=fill,
                             fg="#e8e8e8", font=("Microsoft YaHei", 9, "bold"),
                             anchor="w")
        self.name.pack(side="left", fill="x", expand=True)
        label = uikit.StatusPill.LABELS.get(status, status)
        self.pill = uikit.StatusPill(self.top, text=label, status=status,
                                     width=max(4, len(label) + 2))
        self.pill.pack(side="right", padx=(6, 0))
        self.pill_status = status
        self.meta = tk.Label(self.body, text=meta, bg=fill, fg=theme.MUTED,
                             font=("Microsoft YaHei", 8), anchor="w")
        self.meta.pack(fill="x", pady=(2, 0))
        self.bind("<Configure>", lambda _e: self._repaint())
        self.after_idle(self._repaint)

    def _repaint(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        fill = SEL_BG if self.selected else CARD_BG
        img = uikit.rounded_image(w, h, self.RADIUS, fill,
                                  theme.ACCENT if self.selected else theme.BORDER,
                                  accent=theme.ACCENT if self.selected else None)
        if img is None:
            return
        self._photo = uikit.ImageTk.PhotoImage(img)
        self._art.configure(image=self._photo)

    def set_status(self, status):
        self.pill_status = status
        label = uikit.StatusPill.LABELS.get(status, status)
        self.pill.set_status(status, label)
        self.pill.configure(width=max(4, len(label) + 2))

    def set_selected(self, selected):
        if selected == self.selected:
            return
        self.selected = selected
        bg = SEL_BG if selected else CARD_BG
        self.body.configure(bg=bg)
        for w in (self.top, self.name, self.meta):
            w.configure(bg=bg)
        self._repaint()


class LeftQueuePanel(tk.Frame):
    WIDTH = 208
    HEIGHT = 760

    def __init__(self, master, on_add=None, on_remove=None, on_up=None,
                 on_down=None, on_select=None, on_reorder=None,
                 on_detect=None, on_version=None, on_retry=None,
                 on_autostart=None, on_auto_resume=None, **kw):
        super().__init__(master, bg=theme.NAV_BG, width=self.WIDTH,
                         height=self.HEIGHT, highlightthickness=0, **kw)
        self.pack_propagate(False)
        self.columnconfigure(0, weight=1)
        # 不再画右边缘那条 1px 竖线：左栏底色比右侧面板深一档，本身就分得开，
        # 多一条亮线看着像一条没必要的边框。

        wrap = tk.Frame(self, bg=theme.NAV_BG)
        wrap.pack(side="left", fill="both", expand=True)

        head = tk.Frame(wrap, bg=theme.NAV_BG)
        head.pack(fill="x", padx=12, pady=(12, 8))
        tk.Label(head, text="☰ 渲染队列", bg=theme.NAV_BG, fg=theme.ACCENT,
                 font=("Microsoft YaHei", 10, "bold")).pack(side="left")
        self.count = tk.Label(head, text="3 项", bg="#333338", fg=theme.MUTED,
                              font=("Microsoft YaHei", 8), padx=7, pady=1)
        self.count.pack(side="right")

        # 操作条放在队列标题下面、列表上面；添加按钮用主色实心突出
        actions = tk.Frame(wrap, bg=theme.NAV_BG)
        actions.pack(fill="x", padx=8)
        row = tk.Frame(actions, bg=theme.NAV_BG)
        row.pack(fill="x", pady=(0, 9))
        self.buttons = {}
        specs = [("add", "＋ 添加工程", 92, on_add, "primary"),
                 ("remove", "✕", 26, on_remove, "secondary"),
                 ("up", "↑", 26, on_up, "secondary"),
                 ("down", "↓", 26, on_down, "secondary")]
        for key, text, width, cmd, kind in specs:
            btn = uikit.RoundButton(row, text=text, width=width, height=26,
                                    kind=kind,
                                    command=cmd or (lambda: None))
            btn.pack(side="left", padx=(0, 4))
            self.buttons[key] = btn
        tk.Frame(wrap, bg=theme.BORDER, height=1).pack(fill="x")

        # 队列可能很长：用 Canvas 兜住，滚轮 / 拖动排序都在里面
        self.viewport = tk.Frame(wrap, bg=theme.NAV_BG)
        self.viewport.pack(fill="both", expand=True, padx=8, pady=(8, 8))
        self._canvas = tk.Canvas(self.viewport, bg=theme.NAV_BG,
                                 highlightthickness=0, bd=0)
        self._sb = ttk.Scrollbar(self.viewport, orient="vertical",
                                 command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._sb.pack(side="right", fill="y")
        self.listbox = tk.Frame(self._canvas, bg=theme.NAV_BG)
        self._win = self._canvas.create_window((0, 0), window=self.listbox,
                                               anchor="nw")
        self.listbox.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._win, width=e.width))
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

        self.on_select = on_select
        self.on_reorder = on_reorder
        self._items = []
        self._cards = []
        self._selected = 0
        self._drag_from = None

        self.program = ProgramSettingsPanel(
            wrap, on_detect=on_detect, on_version=on_version,
            on_retry=on_retry, on_autostart=on_autostart,
            on_auto_resume=on_auto_resume)
        self.program.pack(side="bottom", fill="x")

    def set_items(self, items, selected=0):
        self._items = [dict(x) for x in items]
        self._selected = max(0, min(int(selected), len(self._items) - 1)) \
            if self._items else 0
        self._render()

    def _render(self):
        for w in self.listbox.winfo_children():
            w.destroy()
        self._cards = []
        if not self._items:
            tk.Label(self.listbox, text="队列是空的\n点上面的「＋ 添加工程」",
                     bg=theme.NAV_BG, fg=theme.MUTED, justify="left",
                     anchor="w", font=("Microsoft YaHei", 8)).pack(
                         fill="x", padx=4, pady=(2, 0))
        for i, item in enumerate(self._items):
            card = QNavItem(self.listbox, item.get("name", ""),
                            item.get("meta", ""), item.get("status", "pending"),
                            selected=(i == self._selected))
            card.pack(fill="x", pady=(0, 6))
            self._bind_tree(card, i)
            self._cards.append(card)
        self.count.configure(text="%d 项" % len(self._items))

    def _bind_tree(self, widget, index):
        widget.bind("<ButtonPress-1>",
                    lambda e, i=index: self._on_press(e, i))
        widget.bind("<ButtonRelease-1>",
                    lambda e, i=index: self._on_release(e, i))
        for child in widget.winfo_children():
            self._bind_tree(child, index)

    def _on_press(self, event, index):
        self._drag_from = index
        self._drag_y = event.y_root
        self.select(index)

    def _on_release(self, event, _index):
        if self._drag_from is None:
            return
        src = self._drag_from
        self._drag_from = None
        # 只是点选（指针几乎没动）就别重排：点卡片下半截 ≠「往下拖一位」
        if abs(event.y_root - getattr(self, "_drag_y", event.y_root)) < 8:
            return
        dst = self._index_at(event.y_root)
        if dst == src:
            return
        item = self._items.pop(src)
        self._items.insert(dst, item)
        self._selected = dst
        self._render()
        if self.on_reorder:
            self.on_reorder(src, dst)

    def _on_wheel(self, event):
        """只滚指针所在的队列区，别抢右侧内容区的滚轮。"""
        try:
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
            while w is not None:
                if w is self.viewport or w is self._canvas or w is self.listbox:
                    self._canvas.yview_scroll(
                        -3 if event.delta > 0 else 3, "units")
                    return "break"
                w = getattr(w, "master", None)
        except tk.TclError:
            pass
        return None

    def _index_at(self, y_root):
        """指针落在哪张卡片上就返回哪张；拖到列表外再说边界。"""
        if not self._cards:
            return 0
        for i, card in enumerate(self._cards):
            top = card.winfo_rooty()
            if top <= y_root < top + card.winfo_height():
                return i
        first = self._cards[0].winfo_rooty()
        return 0 if y_root < first else len(self._cards) - 1

    def select(self, index, notify=True):
        if not self._items:
            return
        index = max(0, min(int(index), len(self._items) - 1))
        self._selected = index
        for i, card in enumerate(self._cards):
            card.set_selected(i == index)
        if notify:
            if self.on_select:
                self.on_select(index)
            self.event_generate("<<ListboxSelect>>")

    def selected_index(self):
        return self._selected

    # ---- 兼容旧 Listbox 的常用 API（业务层仍按原方式操作队列）----
    def delete(self, _first, _last=None):
        self._items = []
        self._selected = -1
        self._render()

    def insert(self, _index, text):
        parts = [p.strip() for p in str(text).split("|")]
        name = parts[0] if parts else ""
        frames = parts[1] if len(parts) > 1 else ""
        status = parts[2] if len(parts) > 2 else "pending"
        out = parts[3] if len(parts) > 3 else ""
        self._items.append({
            "name": name, "status": status,
            "meta": " · ".join(x for x in (frames, out) if x)})
        if self._selected < 0:
            self._selected = 0
        self._render()

    def itemconfig(self, _index, **_kw):
        return None                 # 新卡片用状态胶囊表达颜色，不再单独改前景色

    def curselection(self):
        return (self._selected,) if 0 <= self._selected < len(self._items) else ()

    def selection_set(self, index):
        self.select(int(index), notify=False)

    def selection_clear(self, _first=None, _last=None):
        self._selected = -1
        for card in self._cards:
            card.set_selected(False)

    def see(self, _index):
        return None

    def size(self):
        return len(self._items)

    def nearest(self, y):
        for i, card in enumerate(self._cards):
            top = card.winfo_y()
            if y < top + card.winfo_height():
                return i
        return max(0, len(self._cards) - 1)
