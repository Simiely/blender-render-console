# -*- coding: utf-8 -*-
"""可复用的 tkinter UI 零件库。

界面层（`gui.py`）只负责「选零件、摆位置、接数据」；这里放的都是**不带业务语义**
的通用控件：卡片模块、队列卡片列表、状态胶囊、滚动区、动作栏、日志面板。

为什么单独一层：之前把布局代码和业务逻辑揉在 `gui.py` 里，每次调结构都要改同一个
巨型方法，改一处牵一片。抽出零件后，视图可以像搭积木一样重组，业务方法只认控件句柄。
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:
    from PIL import Image, ImageColor, ImageDraw, ImageTk
except ImportError:  # 打包时未装 Pillow 会在建按钮时给出明确报错
    Image = ImageColor = ImageDraw = ImageTk = None

from . import theme


def widget_bg(widget, fallback=theme.PANEL2):
    """取控件真实背景：tk 用 cget("bg")，ttk 用当前 style 的 background。"""
    try:
        return widget.cget("bg")
    except tk.TclError:
        pass
    try:
        style_name = widget.cget("style") or widget.winfo_class()
    except tk.TclError:
        style_name = widget.winfo_class()
    try:
        bg = ttk.Style(widget).lookup(style_name, "background")
    except tk.TclError:
        bg = ""
    return bg or fallback


def fit_combobox(cb, values=None, extra=1):
    """按字体实测宽度设置 Combobox 的 `-width`，中文选项不被硬切。"""
    try:
        from tkinter import font as tkfont
        f = tkfont.Font(root=cb, font=cb.cget("font") or "TkDefaultFont")
        unit = max(1, f.measure("0"))
        widest = max(f.measure(str(v)) for v in (values or cb.cget("values")))
        cb.configure(width=int(round(widest / float(unit))) + extra)
    except Exception:
        pass


class IconBadge(tk.Canvas):
    """22×22 圆角色块 + 居中符号（对应 HTML `.card-icon`）。"""

    def __init__(self, master, text="", size=22, radius=6,
                 fill=None, fg=None, bg=None, **kw):
        fill = fill or theme.ACCENT_SOFT
        fg = fg or theme.ACCENT
        bg = bg or theme.PANEL2
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, **kw)
        self._size = size
        self._draw_round_rect(1, 1, size - 1, size - 1, radius, fill)
        self.create_text(size / 2.0, size / 2.0, text=text, fill=fg,
                         font=("Segoe UI Symbol", 9, "bold"))

    def _draw_round_rect(self, x1, y1, x2, y2, r, fill):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, splinesteps=24,
                            fill=fill, outline="")


def rounded_image(w, h, radius=6, fill=theme.FIELD, border=None,
                  accent=None, accent_w=3, ss=4):
    """抗锯齿圆角位图（RGBA，四角透明）。`accent` 是左侧那条竖色条。

    超采样再降采样，边缘没有 Canvas 多边形的像素阶梯；边框在圆角内侧画，
    最后用圆角遮罩裁掉外溢部分，所以外边缘永远是干净的。
    """
    if Image is None:
        return None
    w, h = max(1, int(w)), max(1, int(h))
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if border:
        draw.rounded_rectangle(
            [0, 0, w * ss - 1, h * ss - 1], radius=radius * ss,
            fill=ImageColor.getcolor(fill, "RGBA"),
            outline=ImageColor.getcolor(border, "RGBA"), width=ss)
    else:
        draw.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1],
                               radius=radius * ss,
                               fill=ImageColor.getcolor(fill, "RGBA"))
    if accent:
        draw.rectangle([0, 0, accent_w * ss, h * ss],
                       fill=ImageColor.getcolor(accent, "RGBA"))
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w * ss - 1, h * ss - 1], radius=radius * ss, fill=255)
    img.putalpha(mask)
    return img.resize((w, h), Image.LANCZOS)


class RoundButton(tk.Label):
    """Pillow 抗锯齿圆角按钮：背景图负责圆角，Tk Label 负责文字。

    4 倍超采样后 LANCZOS 降采样，边缘没有 Canvas 的像素阶梯；
    四角透明，Label 背景取父容器真实颜色，因此不会露出方块。
    """

    SECONDARY = dict(fill=theme.FIELD, fg=theme.FG, border="#565660",
                     hover="#484850", pressed="#2a2d2e")
    PRIMARY = dict(fill=theme.ACCENT, fg=theme.ON_ACCENT, border=theme.ACCENT,
                   hover=theme.ACCENT_HOVER, pressed=theme.ACCENT_PRESSED)
    SS = 4  # 超采样倍数

    def __init__(self, master, text="", command=None, width=None, height=26,
                 radius=6, padx=12, font=("Microsoft YaHei", 9),
                 bg=None, canvas_bg=None, kind="secondary", enabled=True,
                 **kw):
        if Image is None:
            raise ImportError("圆角按钮需要 Pillow：pip install Pillow")
        if kind == "primary":
            font = ("Microsoft YaHei", 9, "bold")
        palette = dict(self.PRIMARY if kind == "primary" else self.SECONDARY)
        palette.update(kw.pop("palette", {}) or {})
        try:
            self._bg = canvas_bg or bg or widget_bg(master)
        except Exception:
            self._bg = theme.PANEL2
        self._font = tkfont.Font(font=font)
        self._text = text
        self._command = command
        self._enabled = bool(enabled)
        self._radius = radius
        self._height = height
        self._width = width if width is not None else max(
            28, self._font.measure(text) + padx * 2)
        self._palette = palette
        self._specs = {
            "normal": (palette["fill"], palette["fg"], palette["border"]),
            "hover": (palette["hover"], palette["fg"], palette["border"]),
            "pressed": (palette["pressed"], palette["fg"], palette["border"]),
            "disabled": ("#2d2d2d", "#6e6e6e", theme.BORDER),
        }
        self._photos = {}
        for name, (fill, fg, border) in self._specs.items():
            self._photos[name] = ImageTk.PhotoImage(
                self._rounded(self._width, self._height, radius, fill, border))
        super().__init__(master, image=self._photos["normal"], bg=self._bg,
                         bd=0, highlightthickness=0, takefocus=0,
                         cursor="hand2" if enabled else "arrow")
        self._state = "normal" if enabled else "disabled"
        self._text_label = tk.Label(self, text=text, bd=0,
                                    highlightthickness=0, takefocus=0,
                                    font=self._font,
                                    bg=self._specs[self._state][0],
                                    fg=self._specs[self._state][1])
        self._text_label.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self, self._text_label):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)
        self._apply()

    @classmethod
    def _rounded(cls, w, h, radius, fill, border):
        return rounded_image(w, h, radius, fill, border, ss=cls.SS)

    def _apply(self):
        if not self._enabled:
            self._state = "disabled"
        self.configure(image=self._photos[self._state])
        fill, fg, _ = self._specs[self._state]
        self._text_label.configure(bg=fill, fg=fg,
                                   cursor="hand2" if self._enabled else "arrow")
        self.configure(cursor="hand2" if self._enabled else "arrow")

    def _inside(self):
        x, y = self.winfo_pointerxy()
        return (self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width()
                and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height())

    def _on_enter(self, _e=None):
        if self._enabled:
            self._state = "hover"
            self._apply()

    def _on_leave(self, _e=None):
        if self._enabled and not self._inside():
            self._state = "normal"
            self._apply()

    def _on_press(self, _e=None):
        if self._enabled:
            self._state = "pressed"
            self._apply()

    def _on_release(self, _e=None):
        if not self._enabled:
            return
        inside = self._inside()
        self._state = "hover" if inside else "normal"
        self._apply()
        if inside and self._command:
            self._command()

    def set_text(self, text):
        self._text = text
        self._text_label.configure(text=text)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        if not self._enabled:
            self._state = "disabled"
        elif self._state == "disabled":
            self._state = "normal"
        self._apply()

    def state(self, statespec=None):
        if statespec is None:
            return () if self._enabled else ("disabled",)
        if isinstance(statespec, str):
            statespec = (statespec,)
        self.set_enabled("disabled" not in statespec)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = dict(cnf, **kw)
        state = kw.pop("state", None)
        text = kw.pop("text", None)
        command = kw.pop("command", None)
        if state is not None:
            self.state(state)
        if text is not None:
            self.set_text(text)
        if command is not None:
            self._command = command
        if kw:
            super().configure(**kw)


class StatusPill(tk.Label):
    """小状态胶囊：pending / done / failed / running 各一种颜色。

    背景是 Pillow 画的圆角位图（和按钮、队列卡片同一套抗锯齿），
    文字用 `compound="center"` 叠在图上 —— 胶囊放进圆角卡片里才不显方。
    """

    COLORS = {
        "pending": ("#333338", theme.MUTED),
        "done": ("#25332f", theme.OK),
        "failed": ("#3a2a26", theme.WARN),
        "running": (theme.ACCENT_SOFT, theme.ACCENT),
    }
    LABELS = {"pending": "待跑", "done": "完成", "failed": "失败",
              "running": "渲染中"}
    HEIGHT = 18
    RADIUS = 5
    PADX = 9

    def __init__(self, master, text="", status="pending", width=None, **kw):
        self._status = status
        self._text = text
        self._min_chars = width
        self._font = tkfont.Font(font=("Microsoft YaHei", 8))
        self._host_bg = widget_bg(master, theme.PANEL2)
        self._photo = None
        kw.pop("font", None)
        super().__init__(master, text=text, bd=0, highlightthickness=0,
                         takefocus=0, compound="center",
                         bg=self._host_bg, font=self._font, padx=0, pady=0,
                         **kw)
        self._render()

    def _size(self):
        # +2 是给 hinting 的余量：measure() 报的宽度比实际渲染偶尔窄 1~2px，
        # 不留余量时「渲染中」最后一个字会被 Label 边界切掉一条。
        w = self._font.measure(self._text or "") + self.PADX * 2 + 2
        if self._min_chars:
            w = max(w, self._min_chars * self._font.measure("0") + 2)
        return max(26, w), self.HEIGHT

    def _render(self):
        bg, fg = self.COLORS.get(self._status, self.COLORS["pending"])
        img = rounded_image(self._size()[0], self.HEIGHT, self.RADIUS, bg)
        if img is None:
            self.configure(bg=bg, fg=fg)
            return
        self._photo = ImageTk.PhotoImage(img)
        self.configure(image=self._photo, fg=fg, bg=self._host_bg)

    def set_status(self, status, text=None):
        self._status = status
        if text is not None:
            self._text = text
            self.configure(text=text)
        self._render()

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = dict(cnf, **kw)
        width = kw.pop("width", None)
        if width is not None:
            self._min_chars = width
            self._render()
        text = kw.pop("text", None)
        if text is not None:
            self._text = text
            kw["text"] = text
        if kw:
            super().configure(**kw)
            if text is not None:
                self._render()


class ModuleCard(ttk.Frame):
    """可折叠模块卡片：标题 + 摘要 + 右侧操作按钮 + 最右折叠箭头 + 内容区。"""

    def __init__(self, master, title, icon="", summary="", expanded=True,
                 actions=(), show_toggle=True, header_click=False, **kw):
        super().__init__(master, style="Card.TFrame", **kw)
        self.title = title
        self.expanded = expanded
        self._header_click = header_click
        header = ttk.Frame(self, style="CardHead.TFrame")
        header.pack(fill="x")
        self.header = header
        if icon:
            IconBadge(header, text=icon).pack(side="left", padx=(6, 2))
        ttk.Label(header, text=title, style="Section.TLabel").pack(
            side="left", padx=(2, 4))
        self.summary_label = ttk.Label(header, text=summary, style="Muted.TLabel")
        self._summary_packed = bool(summary)
        if summary:
            self.summary_label.pack(side="left", padx=(4, 2))
        self.actions = {}
        for attr, text, cmd in actions:
            btn = RoundButton(header, text=text, command=cmd, height=26)
            btn.pack(side="right", padx=2, pady=2)
            self.actions[attr] = btn
        self.toggle = None
        if show_toggle:
            # 折叠箭头最后 pack(side="right") → 位于最右
            self.toggle = ttk.Button(header, text="⌃" if expanded else "⌄",
                                     width=2, style="Icon.TButton",
                                     command=self.toggle_card)
            self.toggle.pack(side="right", padx=(2, 4), pady=2)
        elif header_click:
            # 没有箭头按钮的卡片（如「进度与日志」）：点标题行整行即可折叠/展开。
            # 只绑标题和图标，动作按钮自己的点击不受影响。
            header.configure(cursor="hand2")

            def _toggle(_e=None):
                self.toggle_card()

            for child in header.winfo_children():
                if not isinstance(child, RoundButton):
                    child.bind("<Button-1>", _toggle)
        self.body = ttk.Frame(self, style="Card.TFrame")
        if expanded:
            self.body.pack(fill="x", padx=6, pady=(2, 6))

    def toggle_card(self):
        if self.toggle is None and not self._header_click:
            return
        if self.expanded:
            self.body.pack_forget()
            if self.toggle is not None:
                self.toggle.configure(text="⌄")
        else:
            self.body.pack(fill="x", padx=6, pady=(2, 6))
            if self.toggle is not None:
                self.toggle.configure(text="⌃")
        self.expanded = not self.expanded

    def set_summary(self, text):
        self.summary_label.configure(text=text)
        if text and not self._summary_packed:
            self.summary_label.pack(side="left", padx=(4, 2))
            self._summary_packed = True
        elif not text and self._summary_packed:
            self.summary_label.pack_forget()
            self._summary_packed = False


class QueueList(tk.Frame):
    """队列卡片列表——对外保持 `tk.Listbox` 的常用 API，业务层无需改动。

    提供 delete / insert / itemconfig / curselection / selection_set /
    selection_clear / see / nearest，以及 `<<ListboxSelect>>` 事件。
    内部用 Frame 卡片渲染，才能做出定稿里的「名称 + 状态胶囊 + 帧数 + 输出」两行卡片。
    """

    def __init__(self, master, width=190, height=180, **kw):
        super().__init__(master, bg=theme.PANEL2, highlightthickness=1,
                         highlightbackground=theme.BORDER,
                         width=width, height=height, **kw)
        self._cards = []
        self._rows = []
        self._selection = set()
        self.pack_propagate(False)

    def delete(self, first, last=None):
        for card in self._cards:
            card.destroy()
        self._cards = []
        self._rows = []
        self._selection = set()

    def insert(self, index, text):
        parts = [p.strip() for p in str(text).split("|")]
        name = parts[0] if parts else ""
        frames = parts[1] if len(parts) > 1 else ""
        status = parts[2] if len(parts) > 2 else "pending"
        out = parts[3] if len(parts) > 3 else ""
        idx = len(self._cards)
        card = tk.Frame(self, bg=theme.PANEL, highlightthickness=1,
                        highlightbackground=theme.BORDER)
        card.pack(fill="x", padx=6, pady=3)
        top = tk.Frame(card, bg=theme.PANEL)
        top.pack(fill="x")
        name_lbl = ttk.Label(top, text=name, style="Section.TLabel")
        name_lbl.pack(side="left", padx=(6, 4), pady=(4, 0))
        pill = StatusPill(top, text=StatusPill.LABELS.get(status, status),
                          status=status)
        pill.pack(side="right", padx=(4, 6), pady=(4, 0))
        bottom = tk.Frame(card, bg=theme.PANEL)
        bottom.pack(fill="x")
        meta = ttk.Label(bottom, text="%s · %s" % (frames, out),
                         style="Muted.TLabel")
        meta.pack(side="left", padx=(6, 6), pady=(1, 4))
        card.bind("<Button-1>", lambda _e, i=idx: self._select(i))
        for w in (name_lbl, pill, meta, top, bottom):
            w.bind("<Button-1>", lambda _e, i=idx: self._select(i))
        self._cards.append(card)
        self._rows.append({"name": name_lbl, "pill": pill, "meta": meta,
                           "status": status})
        self._paint()

    def _paint(self):
        for i, row in enumerate(self._rows):
            card = self._cards[i]
            selected = i in self._selection
            card.configure(
                highlightbackground=theme.ACCENT if selected else theme.BORDER,
                bg=theme.PANEL2 if selected else theme.PANEL)
            for w in card.winfo_children():
                try:
                    w.configure(bg=theme.PANEL2 if selected else theme.PANEL)
                except Exception:
                    pass

    def _select(self, index):
        self._selection = {index}
        self._paint()
        self.event_generate("<<ListboxSelect>>")

    def size(self):
        return len(self._cards)

    def curselection(self):
        return tuple(sorted(self._selection))

    def selection_clear(self, first, last=None):
        self._selection = set()
        self._paint()

    def selection_set(self, index):
        self._selection = {int(index)}
        self._paint()

    def itemconfig(self, index, **kw):
        row = self._resolve(index)
        if not row:
            return
        if "foreground" in kw:
            row["name"].configure(foreground=kw["foreground"])
        if "text" in kw:
            row["name"].configure(text=kw["text"])

    def see(self, index):
        return                              # 卡片区不滚动，无需滚动到可见

    def nearest(self, y):
        for i, card in enumerate(self._cards):
            top = card.winfo_y()
            bottom = top + card.winfo_height()
            if top <= y <= bottom:
                return i
        return 0 if self._cards else -1

    def _resolve(self, index):
        if index == "end":
            return self._rows[-1] if self._rows else None
        try:
            i = int(index)
        except (TypeError, ValueError):
            return None
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def set_status(self, index, status, text=None):
        """业务层可选调用：把某张卡片的状态胶囊换成 done/failed/pending。"""
        row = self._resolve(index)
        if row:
            row["pill"].set_status(status, text or status)
            row["status"] = status


class ScrollArea(tk.Frame):
    """Canvas + 内层 Frame + 细滚动条，内层宽度跟随视口。

    底色用面板色（与模块卡片同色），模块之间只靠 1px 分隔线断开 ——
    不要留出会露出窗口底色的缝，那看着像没填满的黑边。
    """

    def __init__(self, master, height=None, **kw):
        super().__init__(master, bg=theme.PANEL, highlightthickness=0, bd=0, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                bg=theme.PANEL)
        sb = ttk.Scrollbar(self, orient="vertical", style="Panel.Vertical.TScrollbar",
                           command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=theme.PANEL)
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        if height:
            self.canvas.configure(height=height)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def on_inner(_e=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        def on_canvas(e):
            self.canvas.itemconfigure(win, width=e.width)
            on_inner()

        self.inner.bind("<Configure>", on_inner)
        self.canvas.bind("<Configure>", on_canvas)

        # 鼠标滚轮：只滚动指针所在的那个 ScrollArea，避免多个滚动区互相抢。
        # 记住绑定 id，销毁时只解掉自己那一条 —— unbind_all 会把别的模块
        # （ui_leftqueue 也绑了 <MouseWheel>）的绑定一起清掉。Tk 8.6 没有按 id
        # 解绑的 API，只能在 Tcl 脚本层把含自己 funcid 的那行摘掉。
        self._wheel_bind_id = self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind("<Destroy>", self._on_destroy)

    def _on_wheel(self, event):
        try:
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
            while w is not None:
                if w is self:
                    step = -1 if event.delta > 0 else 1
                    self.canvas.yview_scroll(step * 3, "units")
                    return "break"
                w = getattr(w, "master", None)
        except tk.TclError:
            pass
        return None

    def _on_destroy(self, _event=None):
        # Tk 8.6 的 unbind_all(sequence) 只收一个参数，也没法按 bind id 精确解绑；
        # 直接 unbind_all 又会连 ui_leftqueue 的全局滚轮绑定一起清掉。
        # bind_all(..., add="+") 返回的 funcid 出现在 Tcl 绑定脚本的对应行里，
        # 逐行摘掉自己那条即可，别的绑定原样保留。
        try:
            script = self.tk.call("bind", "all", "<MouseWheel>")
            if script:
                keep = [line for line in script.split("\n")
                        if "[%s " % self._wheel_bind_id not in line]
                self.tk.call("bind", "all", "<MouseWheel>", "\n".join(keep))
        except (tk.TclError, AttributeError):
            pass


class ActionBar(ttk.Frame):
    """一排动作按钮（日志标题行、队列底部都用它）。"""

    def __init__(self, master, actions=(), **kw):
        super().__init__(master, style="CardHead.TFrame", **kw)
        self.buttons = {}
        for attr, text, cmd in actions:
            btn = RoundButton(self, text=text, command=cmd, height=26)
            btn.pack(side="left", padx=2, pady=3)
            self.buttons[attr] = btn
