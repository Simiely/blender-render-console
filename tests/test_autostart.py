# -*- coding: utf-8 -*-
"""开机自启的测试。

⚠️ **绝不碰真实注册表**：全程用假 backend 替换 `autostart.backend()`。
往用户的 `HKCU\\...\\Run` 写测试项是决不允许的副作用。
"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from brconsole import autostart                                    # noqa: E402


class FakeRegistry(object):
    """假注册表：只认一个 Run 键里的值，同时记住做过哪些操作。"""

    def __init__(self, initial=None, fail_on_set=None):
        self.values = dict(initial or {})
        self.fail_on_set = fail_on_set
        self.calls = []

    def get(self, name):
        self.calls.append(("get", name))
        return self.values.get(name)

    def set(self, name, value):
        self.calls.append(("set", name, value))
        if self.fail_on_set:
            raise RuntimeError(self.fail_on_set)
        self.values[name] = value

    def delete(self, name):
        self.calls.append(("delete", name))
        if name in self.values:
            del self.values[name]
            return True
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.reg = FakeRegistry()
        autostart.set_backend(self.reg)

    def tearDown(self):
        autostart.set_backend(None)          # 恢复真实后端，别把假注册表留给别人


class TestCommand(Base):
    def test_flag_and_value_name(self):
        self.assertEqual(autostart.AUTOSTART_FLAG, "--autostart")
        self.assertEqual(autostart.VALUE_NAME, "blender-render-console")

    def test_source_mode_points_at_main_py(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            cmd = autostart.launch_command()
        self.assertIn("main.py", cmd)
        self.assertIn("--autostart", cmd)
        self.assertTrue(cmd.startswith('"'))          # 路径要带引号，防空格

    def test_frozen_mode_is_the_exe_itself(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.object(sys, "executable", r"C:\apps\brc\brc.exe"):
                cmd = autostart.launch_command()
        self.assertEqual(cmd, r'"C:\apps\brc\brc.exe" --autostart')

    def test_project_root_is_above_package(self):
        root = autostart.project_root()
        self.assertTrue(os.path.exists(os.path.join(root, "main.py")))


class TestToggle(Base):
    def test_starts_disabled(self):
        self.assertFalse(autostart.is_enabled())
        self.assertIsNone(autostart.registered_command())
        self.assertFalse(autostart.is_current())

    def test_enable_then_disable(self):
        cmd = autostart.enable()
        self.assertEqual(self.reg.values[autostart.VALUE_NAME], cmd)
        self.assertTrue(autostart.is_enabled())
        self.assertTrue(autostart.is_current())
        self.assertTrue(autostart.disable())
        self.assertFalse(autostart.is_enabled())

    def test_is_current_detects_moved_exe(self):
        """换过目录后，开机拉起来的是**另一个**程序 —— 界面必须能察觉并提示重登记。"""
        self.reg.values[autostart.VALUE_NAME] = r'"D:\old\brc.exe" --autostart'
        self.assertTrue(autostart.is_enabled())
        self.assertFalse(autostart.is_current())

    def test_blank_value_counts_as_off(self):
        self.reg.values[autostart.VALUE_NAME] = "   "
        self.assertIsNone(autostart.registered_command())
        self.assertFalse(autostart.is_enabled())

    def test_disable_when_absent(self):
        self.assertFalse(autostart.disable())


class TestSync(Base):
    def test_enable(self):
        ok, msg = autostart.sync(True)
        self.assertTrue(ok, msg)
        self.assertIn("已登记", msg)
        self.assertTrue(autostart.is_enabled())

    def test_disable(self):
        autostart.enable()
        ok, msg = autostart.sync(False)
        self.assertTrue(ok, msg)
        self.assertIn("已取消", msg)
        self.assertFalse(autostart.is_enabled())

    def test_disable_when_already_off_is_not_an_error(self):
        ok, msg = autostart.sync(False)
        self.assertTrue(ok, msg)
        self.assertIn("本来就是关的", msg)

    def test_failure_is_reported_not_raised(self):
        """注册表写不进去（组策略/权限）时要**返回错误**，让界面能回滚勾选。"""
        self.reg = FakeRegistry(fail_on_set="拒绝访问")
        autostart.set_backend(self.reg)
        ok, msg = autostart.sync(True)
        self.assertFalse(ok)
        self.assertIn("拒绝访问", msg)
        self.assertFalse(autostart.is_enabled())

    @unittest.skipIf(os.name == "nt", "只在非 Windows 上验证「不支持」分支")
    def test_unsupported_platform(self):
        ok, msg = autostart.sync(True)
        self.assertFalse(ok)
        self.assertIn("不支持", msg)


if __name__ == "__main__":
    unittest.main()
