# -*- coding: utf-8 -*-
"""power 模块测试。要害：动作只到"拼命令"为止（绝不真关机）+ 幂等 + 失败不抛。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole import power  # noqa: E402


class _FakeBackend(object):
    def __init__(self, fail=False, rc=0):
        self.calls = []
        self.fail = fail
        self.rc = rc

    def shutdown(self, seconds):
        self.calls.append(("shutdown", seconds))
        if self.fail:
            raise OSError("boom")
        return _Result(self.rc)

    def sleep(self):
        self.calls.append(("sleep",))
        if self.fail:
            raise RuntimeError("boom")
        return _Result(self.rc)

    def abort_shutdown(self):
        self.calls.append(("abort",))
        if self.fail:
            raise RuntimeError("boom")
        return _Result(self.rc)


class _Result(object):
    def __init__(self, rc=0, stderr=b""):
        self.returncode = rc
        self.stderr = stderr


class TestAftermathActions(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeBackend()
        self._old = power.backend()
        power.set_backend(self.fake)

    def tearDown(self):
        power.set_backend(None)

    def test_shutdown_passes_countdown_seconds(self):
        ok, msg = power.shutdown_after(60)
        self.assertTrue(ok)
        self.assertEqual(self.fake.calls, [("shutdown", 60)])
        self.assertIn("60", msg)

    def test_shutdown_failure_returns_message_not_raise(self):
        self.fake.fail = True
        ok, msg = power.shutdown_after(60)
        self.assertFalse(ok)
        self.assertIn("OSError", msg)

    def test_shutdown_nonzero_rc_reported(self):
        self.fake.rc = 1190
        ok, msg = power.shutdown_after(60)
        self.assertFalse(ok)
        self.assertIn("1190", msg)

    def test_sleep_and_abort(self):
        power.sleep_now()
        power.abort_shutdown()
        self.assertEqual(self.fake.calls, [("sleep",), ("abort",)])


class TestCommandBackendShape(unittest.TestCase):
    """真实后端只验"命令拼装对不对"，不真执行 —— 用注入替换 subprocess.run。"""

    def test_shutdown_command_shape(self):
        import brconsole.power as p
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _Result(0)

        old = p.subprocess.run
        try:
            p.subprocess.run = fake_run
            p.CommandBackend().shutdown(60)
        finally:
            p.subprocess.run = old
        self.assertEqual(seen["cmd"], ["shutdown", "/s", "/t", "60"])

    def test_abort_command_shape(self):
        import brconsole.power as p
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _Result(0)

        old = p.subprocess.run
        try:
            p.subprocess.run = fake_run
            p.CommandBackend().abort_shutdown()
        finally:
            p.subprocess.run = old
        self.assertEqual(seen["cmd"], ["shutdown", "/a"])


@unittest.skipUnless(power.supported(), "SetThreadExecutionState 只在 Windows")
class TestKeepAwake(unittest.TestCase):
    """真实 API 调用（无害：只设置/清除执行状态标志，不改变系统设置）。"""

    def test_keep_then_allow_is_idempotent(self):
        self.assertTrue(power.keep_awake())
        self.assertTrue(power.keep_awake())        # 重复调用不炸
        self.assertTrue(power.allow_sleep())
        self.assertTrue(power.allow_sleep())       # 清除两次也不炸


if __name__ == "__main__":
    unittest.main()
