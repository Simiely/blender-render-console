# -*- coding: utf-8 -*-
"""notify 模块测试。要害：三层各自独立、任何一层失败都不影响其他层、不真响不真弹。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole import notify  # noqa: E402


class _FakeBackend(object):
    def __init__(self, fail_beep=False, fail_flash=False, fail_toast=False):
        self.calls = []
        self.fail_beep = fail_beep
        self.fail_flash = fail_flash
        self.fail_toast = fail_toast

    def beep(self):
        self.calls.append("beep")
        if self.fail_beep:
            raise OSError("boom")
        return True

    def flash(self, tk_winfo_id):
        self.calls.append(("flash", tk_winfo_id))
        if self.fail_flash:
            raise RuntimeError("boom")
        return True

    def toast(self, title, message):
        self.calls.append(("toast", title, message))
        if self.fail_toast:
            raise RuntimeError("boom")
        return True


class TestAnnounce(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeBackend()
        notify.set_backend(self.fake)

    def tearDown(self):
        notify.set_backend(None)

    def test_all_layers_reported(self):
        r = notify.announce(12345, "标题", "内容")
        self.assertEqual(r, {"beep": True, "flash": True, "toast": True})
        self.assertEqual(self.fake.calls[1], ("flash", 12345))
        self.assertEqual(self.fake.calls[2], ("toast", "标题", "内容"))

    def test_one_failure_does_not_break_the_rest(self):
        """闪烁炸了，提示音和 toast 照发 —— 通知层永远不把主流程搞崩。"""
        self.fake.fail_flash = True
        r = notify.announce(12345, "标题", "内容")
        self.assertEqual(r["beep"], True)
        self.assertEqual(r["flash"], False)
        self.assertEqual(r["toast"], True)
        self.assertEqual(len(self.fake.calls), 3)

    def test_toast_can_be_skipped(self):
        notify.announce(12345, "标题", "内容", toast=False)
        self.assertNotIn(("toast", "标题", "内容"), self.fake.calls)

    def test_no_window_id_skips_flash(self):
        notify.announce(None, "标题", "内容")
        kinds = [c if isinstance(c, str) else c[0] for c in self.fake.calls]
        self.assertEqual(kinds, ["beep", "toast"])


class TestWinBackendToastScript(unittest.TestCase):
    """真实后端的 toast 只验"命令拼装对不对"，不真弹。"""

    def test_powershell_command_shape(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _R(0)

        class _R(object):
            def __init__(self, rc):
                self.returncode = rc

        old = notify.subprocess.run
        try:
            notify.subprocess.run = fake_run
            ok = notify.WinBackend().toast("渲完了", "6/6 帧")
        finally:
            notify.subprocess.run = old
        self.assertTrue(ok)
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "powershell")
        self.assertIn("-NoProfile", cmd)
        script = cmd[-1]
        self.assertIn("ToastNotificationManager", script)
        self.assertIn("渲完了", script)
        self.assertIn("6/6 帧", script)


if __name__ == "__main__":
    unittest.main()
