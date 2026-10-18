# -*- coding: utf-8 -*-
"""主题层里**不依赖 tkinter 就能测**的部分。

这块踩过一个很典型的坑：ttk 的样式选项是"通用数据库 + 元素自己挑着用"——
配一个元素不认识的选项**不报错，只静默失效**。
本项目给 `TCheckbutton` 配过 `indicatorcolor`，而 clam 的指示器元素只认
`indicatorbackground / indicatorforeground / indicatorsize / indicatormargin /
upperbordercolor / lowerbordercolor`（Tk 源码
`generic/ttk/ttkClamTheme.c::IndicatorElementOptions`）——
那行配置从写下那天起就没生效过，界面上完全看不出来，也没有任何测试能发现。

这里把"配了不生效"这件事变成可检出的。
"""

import inspect
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from brconsole import theme  # noqa: E402


class _FakeStyle(object):
    """只实现 `element_options` —— 真正的 ttk.Style 需要 Tk 才能建，用替身就够验逻辑。"""

    def __init__(self, options):
        self._options = list(options)

    def element_options(self, element):
        return list(self._options)


class TestOptionSupport(unittest.TestCase):
    def test_detects_options_the_element_does_not_have(self):
        style = _FakeStyle(["-indicatorbackground", "-indicatorforeground", "-indicatorsize"])
        missing = theme.unsupported_options(
            style, "Checkbutton.indicator",
            ["indicatorcolor", "indicatorbackground", "indicatorsize"])
        self.assertEqual(missing, ["indicatorcolor"])

    def test_all_supported_gives_empty(self):
        style = _FakeStyle(["-indicatorbackground"])
        self.assertEqual(
            theme.unsupported_options(style, "x", ["indicatorbackground"]), [])

    def test_unknown_element_does_not_raise(self):
        """元素不存在时把整批当"不支持"，而不是抛出去 —— 这是自检，不该反过来弄崩界面。"""

        class Boom(object):
            def element_options(self, name):
                raise RuntimeError          # 元素不存在时 ttk 抛的就是 TclError 一类

        self.assertEqual(theme.unsupported_options(Boom(), "x", ["a", "b"]), ["a", "b"])


class TestNoDeadCheckbuttonOption(unittest.TestCase):
    def test_theme_does_not_configure_indicatorcolor(self):
        """clam 的指示器元素不支持 `indicatorcolor` —— 配了就是死代码，还会误导后来的人。

        允许它在注释/docstring 里出现（那里正是在解释这个坑），
        只禁止 `indicatorcolor=` 这种**真的去配**的写法。
        """
        src = inspect.getsource(theme).replace(" ", "")
        self.assertNotIn("indicatorcolor=", src)

    def test_check_element_is_used_by_the_layout(self):
        """自绘元素建出来之后必须真的被 `layout()` 用上，否则等于没换。"""
        src = inspect.getsource(theme)
        self.assertIn("style.element_create(CHECK_ELEMENT", src)
        self.assertIn("(CHECK_ELEMENT,", src)


class _FakeStyleWithElements(_FakeStyle):
    """再补上建元素/装布局/配样式的几个方法，用来测 `install_*` 干了什么。"""

    def __init__(self, options=()):
        _FakeStyle.__init__(self, options)
        self.created = []
        self.layouts = []
        self.configures = []
        self.maps = []

    def element_delete(self, name):
        raise RuntimeError                  # 第一次 apply 时元素还不存在，ttk 就是抛错

    def element_create(self, *args, **kwargs):
        self.created.append((args, kwargs))

    def layout(self, name, spec=None):
        if spec is not None:
            self.layouts.append((name, spec))
        return []

    def configure(self, name, **kwargs):
        self.configures.append((name, kwargs))

    def map(self, name, **kwargs):
        self.maps.append((name, kwargs))


class TestIndicatorImagesAreKeptAlive(unittest.TestCase):
    """自绘对勾的图片引用必须挂在**模块级**池子里，不能只挂在 Style 实例上。

    踩过的坑（改了颜色还是看不见框，最后一查是这个）：`install_check_indicator`
    最初写的是 `style._brc_check_images = (on, off)`，而 `run_gui()` 里是
    `theme.apply(root)` —— 返回值没人接，`Style` 实例出了函数就被回收，
    两个 `PhotoImage` 跟着被删；ttk 的 image 元素只记 Tcl 侧的图像名、不持有
    Python 对象，于是元素**尺寸还在、什么都画不出来**：4 个复选框的位置还占着，
    框和勾全没了，也不报任何错。截图里看就是"复选框凭空消失"。
    """

    def setUp(self):
        self._saved = theme.check_indicator_images()
        self.addCleanup(self._restore)

    def _restore(self):
        theme._IMAGE_REFS[:] = list(self._saved)      # noqa: SLF001 - 测试就地还原全局池

    def _install(self, fake_style):
        """跑一遍安装流程，`_check_image` 换成哨兵对象（否则要真 Tk 才能建图片）。"""
        made = []

        def fake_image(master, size, checked):
            img = object()
            made.append(img)
            return img

        with mock.patch.object(theme, "_check_image", fake_image):
            theme.install_check_indicator(None, fake_style)
        return made

    def test_images_live_in_a_module_level_pool(self):
        style = _FakeStyleWithElements()
        made = self._install(style)
        self.assertEqual(theme.check_indicator_images(), tuple(made))
        self.assertFalse(hasattr(style, "_brc_check_images"),
                         "图片只挂在 Style 上时，apply() 的返回值一被丢弃它们就被回收了")

    def test_pool_is_reset_not_grown_on_reapply(self):
        self._install(_FakeStyleWithElements())
        made = self._install(_FakeStyleWithElements())
        self.assertEqual(theme.check_indicator_images(), tuple(made))
        self.assertEqual(len(theme.check_indicator_images()), 2)

    def test_layout_swaps_in_the_custom_element(self):
        style = _FakeStyleWithElements()
        self._install(style)
        self.assertEqual(len(style.layouts), 1, "只该动 TCheckbutton 一个类的布局")
        name, spec = style.layouts[0]
        self.assertEqual(name, "TCheckbutton")
        self.assertIn(theme.CHECK_ELEMENT, repr(spec))
        self.assertNotIn("Checkbutton.indicator", repr(spec))


class TestScrollbarIsSlimAndArrowless(unittest.TestCase):
    """日志区右侧的滚动条要"细杆、无箭头"。

    原来的 clam 默认布局是 `trough > (uparrow, downarrow, thumb)`：深色底上那两个
    三角形箭头 + 滑块的 1px 亮/暗棱线，看起来像"断成两截的色块"，比日志正文还抢眼。
    这里断言的就是"箭头不在布局里"——元素留在样式库无所谓，**不进布局就不会被画、也不占宽度**。
    """

    def _install(self, thickness=10):
        style = _FakeStyleWithElements()
        theme.install_scrollbar(style, thickness)
        return style

    def test_no_arrow_elements_in_layout(self):
        style = self._install()
        self.assertEqual(len(style.layouts), 4, "竖/横 × 常态/空闲态 共 4 套")
        for name, spec in style.layouts:
            flat = repr(spec)
            self.assertIn("trough", flat)
            self.assertIn("thumb", flat)
            self.assertNotIn("arrow", flat, "%s 的布局里不该还有箭头" % name)

    def test_idle_style_is_invisible_and_same_thickness(self):
        """空闲态滑块与槽同色（=看不见），而且杆宽必须和常态完全一致 —— 否则换样式会挪布局。"""
        style = self._install(10)
        by_name = dict(style.configures)
        normal = by_name[theme.SCROLL_STYLE]
        flat = by_name[theme.SCROLL_FLAT_STYLE]
        self.assertEqual(flat["background"], flat["troughcolor"])
        self.assertNotEqual(normal["background"], normal["troughcolor"])
        self.assertEqual(normal["arrowsize"], flat["arrowsize"])

    def test_thickness_goes_through_arrowsize(self):
        """clam 用 `-arrowsize` 算滚动条横截面 —— 没有箭头时它就等于杆宽（实测 8/10/12 → 同宽）。"""
        style = self._install(12)
        for name, kwargs in style.configures:
            self.assertEqual(kwargs["arrowsize"], 12, name)
            self.assertEqual(kwargs["borderwidth"], 0, "细杆上再加边框就白细了")

    def test_thumb_is_flat_not_bevelled(self):
        """lightcolor/darkcolor 必须和滑块同色，否则细杆上那两条棱线正好占满宽度。"""
        style = self._install()
        for name, kwargs in style.configures:
            self.assertEqual(kwargs["lightcolor"], kwargs["background"], name)
            self.assertEqual(kwargs["darkcolor"], kwargs["background"], name)
            self.assertEqual(kwargs["gripcount"], 0, "10px 的杆上画防滑横线像个小梯子")

    def test_hover_and_pressed_states_exist(self):
        style = self._install()
        for name, kwargs in style.maps:
            states = dict(kwargs["background"])
            self.assertIn("active", states, name)
            self.assertIn("pressed", states, name)


if __name__ == "__main__":
    unittest.main()
