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
#: 需要用户注意的那类提示（帧范围填错了之类）—— 深色底上要够亮才看得清
WARN = "#f48771"

# 日志区（等宽字体，比正文更暗一点）
LOG_BG = "#181818"
LOG_FG = "#cfcfcf"

#: 滚动条两种样式：常态（看得见滑块）与"空闲态"（滑块与槽同色，等于看不见）。
#: 内容装得下的时候 ttk 会把滑块拉满整条槽 —— 那会变成一条常驻的亮灰色长条，
#: 比正文还显眼。界面按日志的可见范围在两者之间切换（见 gui.App._on_log_scroll）。
SCROLL_STYLE = "Vertical.TScrollbar"
SCROLL_FLAT_STYLE = "Flat.Vertical.TScrollbar"

#: 滚动条的杆宽基准（96 DPI 下的像素），按 tk scaling 换算，与复选框同一套换算
SCROLL_BASE_PX = 10
#: 滑块三态：常态 / 悬停 / 按住（越按越亮，和界面里按钮的反馈方向一致）
SCROLL_THUMB = "#4a4a4a"
SCROLL_THUMB_HOVER = "#5f5f5f"
SCROLL_THUMB_PRESSED = "#8a8a8a"

#: 复选框指示器的边长基准（96 DPI 下的像素）。实际尺寸按 tk scaling 换算 ——
#: 图片元素**不会**跟着 DPI 放大，写死 13px 在 125% 缩放下会比文字小一圈。
CHECK_BASE_PX = 13
#: 未选中时的框线色（比 BORDER 亮一点，否则深色底上根本看不见框）
CHECK_BORDER = "#6e6e6e"
#: 选中时的对勾颜色（压在 ACCENT 蓝底上，要用白色才够亮）
CHECK_ON = "#ffffff"

#: 自绘指示器元素的名字（`element_create` 用）。带前缀避免和主题自带的撞名。
CHECK_ELEMENT = "Brc.Check.indicator"

#: 自绘指示器图片的**引用池**（模块级，别改挂到 Style 实例上）。
#:
#: ttk 的 image 元素只记住 Tcl 侧的图像名，**不持有 Python 对象**；`PhotoImage`
#: 一旦被 GC，Tcl 图像就被 `image delete` 掉，元素立刻变成一片空白（尺寸还在、
#: 什么都不画，界面上看起来就是"复选框消失"）。
#:
#: 这里踩过一次：最早把引用挂在 `style` 上（`style._brc_check_images = ...`），
#: 结果 `run_gui()` 里写的是 `theme.apply(root)` —— 返回值没人接，`Style` 实例
#: 出了函数就被回收，图片跟着没，4 个复选框全隐身。挂模块级变量才不依赖调用方
#: 是否留着 Style。
_IMAGE_REFS = []


def check_indicator_images():
    """当前的自绘指示器图片（未选中, 选中）—— 给测试/探针用，界面逻辑不用它。"""
    return tuple(_IMAGE_REFS)


def unsupported_options(style, element, options):
    """挑出 `options` 里**该元素并不支持**的那些。

    存在的意义：ttk 的样式选项是"通用数据库 + 元素自己挑着用"——配一个元素不认识的
    选项**不会报错，只会静默失效**。本项目真踩过：`theme.py` 给 `TCheckbutton` 配了
    `indicatorcolor`，而 clam 的指示器元素只认 `-indicatorbackground/-indicatorforeground/
    -indicatorsize/-indicatormargin/-upper-lowerbordercolor`（见 Tk 源码
    `generic/ttk/ttkClamTheme.c::IndicatorElementOptions`），于是那行配置从写下的
    那天起就没生效过，界面上完全看不出来。

    `style` 只要求有 `element_options(name)` 方法，所以单测能传一个替身进来。
    """
    try:
        supported = {str(o).lstrip("-") for o in style.element_options(element)}
    except Exception:
        return list(options)
    return [o for o in options if str(o).lstrip("-") not in supported]


def _dpi_scale(root):
    """当前 DPI 相对 96 的倍数（`tk scaling` 是"每磅多少像素"，96 DPI ≈ 1.333）。"""
    try:
        raw = float(root.tk.call("tk", "scaling")) / (96.0 / 72.0)
    except Exception:
        raw = 1.0
    return max(1.0, min(3.0, raw))


def _put_stroke(img, points, color, width=2):
    """在 PhotoImage 上画一条折线（逐像素贴方块）—— 零依赖，不需要 Pillow。

    复选框那么小的尺寸用不上抗锯齿，直接把线段"盖章"成方块反而边界清楚。

    ⚠️ `width` 是**实际像素宽度**：早期写成 `to=(x-r, y-r, x+r+1, y+r+1)`，
    `width=2` 会盖出 3×3 的方块 —— 13px 的框里就粗得糊成一团（看起来像"实心块"
    而不是对勾）。现在按 `width×width` 精确盖。
    """
    w, h = img.width(), img.height()
    half = width // 2
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 2))
        for k in range(steps + 1):
            t = k / float(steps)
            x = int(round(x0 + (x1 - x0) * t)) - half
            y = int(round(y0 + (y1 - y0) * t)) - half
            img.put(color, to=(max(0, x), max(0, y),
                               min(w, x + width), min(h, y + width)))


def _check_image(master, size, checked):
    """画一张 `size×size` 的复选框指示器：空框，或实心强调色框 + 白对勾。

    配色跟界面其余部分对齐：
    - 未选中：`FIELD` 底 + 略亮的边框 —— 和输入框一个观感，一眼看出"这里能点"
    - 选中：`ACCENT` 底 + 白勾 —— 和强调按钮同一个蓝，扫一眼就知道勾没勾
    （早先想过白底黑勾，但深色界面上四个白方块太抢眼，像贴了四张纸。）
    """
    import tkinter as tk

    img = tk.PhotoImage(master=master, width=size, height=size)
    # 先铺底色（PhotoImage 默认透明，但图片元素在部分平台上会给透明像素乱填色，
    # 铺成本控件的面板色最稳 —— 复选框都贴在 PANEL 底上）
    img.put(ACCENT if checked else FIELD, to=(0, 0, size, size))

    edge = ACCENT if checked else CHECK_BORDER
    img.put(edge, to=(0, 0, size, 1))                  # 上
    img.put(edge, to=(0, size - 1, size, size))        # 下
    img.put(edge, to=(0, 0, 1, size))                  # 左
    img.put(edge, to=(size - 1, 0, size, size))        # 右

    if checked:
        # 对勾：左肩 → 谷底 → 右肩（都按边长取比例，任何尺寸下形状一致）
        _put_stroke(img, [(size * 0.24, size * 0.52),
                          (size * 0.43, size * 0.73),
                          (size * 0.77, size * 0.27)], CHECK_ON, width=2)
    return img


def install_check_indicator(root, style):
    """把 `TCheckbutton` 的指示器换成**自绘的对勾**。

    为什么必须换掉而不是改颜色：clam 主题的选中标记是**两条对角线画出来的叉**
    （Tk 源码 `generic/ttk/ttkClamTheme.c::CheckIndicatorElementDraw`：选中时
    `XDrawLine(p,q,r,s)` + `XDrawLine(p,s,r,q)`），Tk 里根本没有"对勾"这个字形。
    所以无论怎么配色，看到的都是一个叉。

    换法走 ttk 的官方机制：`element_create(..., "image", ...)` 建一个新元素，
    再用 `layout()` 把它顶替掉原来的 `Checkbutton.indicator`。这样 4 处 Checkbutton
    （断点续跑 / 原生输出 / 开机自启 / 自动续跑）不用改一行，键盘与 focus 也照旧。
    """
    try:
        style.element_delete(CHECK_ELEMENT)      # 重复 apply 时先删，否则同名元素会撞
    except Exception:
        pass

    size = max(11, int(round(CHECK_BASE_PX * _dpi_scale(root))))
    off = _check_image(root, size, False)
    on = _check_image(root, size, True)
    style.element_create(CHECK_ELEMENT, "image", off, ("selected", on))
    style.layout("TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            (CHECK_ELEMENT, {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]}),
        ]}),
    ])
    # 图片被 GC 回收后元素会变成空白 —— 引用必须由模块级池子持住（见 _IMAGE_REFS）。
    # 每次 apply 都重置一次：重复调用不该让池子无限变长。
    _IMAGE_REFS[:] = [off, on]
    return size


def install_scrollbar(style, thickness=None):
    """把滚动条换成**细杆、无箭头**的样式（VS Code 那种）。

    为什么换：clam 自带的滚动条是"两端各一个三角形箭头 + 中间带 3D 棱线的滑块"，
    深色底上那两条棱线看起来像**两段断开的色块**，加上箭头更吵 —— 日志正文还没它显眼，
    滚动条本来是"辅助"的，抢了主体的注意力就本末倒置了。

    换法仍是 ttk 的官方机制：重写 `layout()`，只留 `trough` + `thumb` 两个元素
    （箭头元素不放进布局，自然就不画、也不占宽度），粗细由 `-arrowsize` 决定 ——
    这个选项名看着是"箭头的尺寸"，实际是 clam 滚动条元素算**横截面尺寸**用的，
    没有箭头时它就等于杆宽（实测 `arrowsize=8/10/12` → 控件宽 8/10/12px）。

    代价：没有箭头按钮了。但滚轮、拖动滑块、点槽翻页都还在 —— 这三点覆盖了实际用法，
    而箭头在 10px 细杆上本来也点不准。

    另外每个方向都铺两套：常态 + `Flat.` 开头的"空闲态"（滑块与槽同色 = 看不见）。
    内容装得下时 ttk 会把滑块拉满整条槽，一条 10px 的亮灰长条常驻在日志右边，
    又变成新的噪音。两套**杆宽完全一致**，界面换样式不动布局 —— 换成"藏控件"的话，
    日志会宽出 10px，`wrap="word"` 立刻重排，行数一变又可能反过来改变"要不要藏"的判定。
    """
    thick = SCROLL_BASE_PX if thickness is None else thickness
    for prefix, stick in (("Vertical", "ns"), ("Horizontal", "we")):
        for flat, thumb in ((False, SCROLL_THUMB), (True, LOG_BG)):
            name = "%s%s.TScrollbar" % ("Flat." if flat else "", prefix)
            style.layout(name, [
                ("%s.Scrollbar.trough" % prefix, {"sticky": stick, "children": [
                    ("%s.Scrollbar.thumb" % prefix, {"expand": "1", "sticky": "nswe"}),
                ]}),
            ])
            # lightcolor/darkcolor 必须一起设成滑块色：clam 的滑块会画 1px 亮边 + 1px 暗边，
            # 只设 background 的话，细杆上那两条棱线正好占满宽度 —— 就是旧样式看着
            # "断成两截"的原因。三色相同 = 纯色平杆。
            # gripcount=0 关掉滑块中间那几道防滑横线：10px 的杆上它看起来像个小梯子。
            style.configure(name, troughcolor=LOG_BG, background=thumb,
                            bordercolor=LOG_BG, lightcolor=thumb, darkcolor=thumb,
                            arrowcolor=LOG_BG, arrowsize=thick, gripcount=0,
                            borderwidth=0, relief="flat")
            if flat:
                # 空闲态不需要任何反馈（滑块本来就看不见），状态全保持同色，
                # 否则鼠标扫过日志区时那条"隐形的杆"会突然亮一下
                style.map(name, background=[("pressed", LOG_BG), ("active", LOG_BG)])
            else:
                style.map(name, background=[("pressed", SCROLL_THUMB_PRESSED),
                                            ("active", SCROLL_THUMB_HOVER)])
    return thick


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
    style.configure("Warn.TLabel", background=PANEL, foreground=WARN)

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

    # ⚠️ 这里**不要**配 `indicatorcolor`：clam 的指示器元素不认这个选项，
    #    配了会被 ttk 静默忽略（曾经就是这么白配了一年多，见 unsupported_options 的说明）。
    #    选中标记换成自绘的对勾，见 install_check_indicator()。
    style.configure("TCheckbutton", background=PANEL, foreground=FG)
    style.map("TCheckbutton", background=[("active", PANEL)])
    install_check_indicator(root, style)

    style.configure("TProgressbar", background=ACCENT, troughcolor=FIELD,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

    # 滚动条：细杆 + 无箭头（原来那套 background/troughcolor 是配给 clam 默认布局的，
    # 而默认布局带上下箭头和 3D 棱线，深色底上会看成"断成两截的色块"。见 install_scrollbar）
    install_scrollbar(style, max(8, int(round(SCROLL_BASE_PX * _dpi_scale(root)))))

    style.configure("TSeparator", background=BORDER)
    style.configure("TRadiobutton", background=PANEL, foreground=FG)

    return style


def log_widget_colors():
    """日志 Text 控件（Tk 原生，不走 ttk）的配色。"""
    return {"background": LOG_BG, "foreground": LOG_FG,
            "insertbackground": FG, "selectbackground": ACCENT}
