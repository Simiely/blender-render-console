# -*- coding: utf-8 -*-
"""ScrollArea 销毁时的全局滚轮绑定语义（Tk 8.6 只能按脚本行精确解绑）。"""

import unittest

from brconsole import uikit


class FakeTk(object):
    def __init__(self, script):
        self.script = script
        self.calls = []

    def call(self, *args):
        self.calls.append(args)
        if len(args) == 3:
            return self.script
        return ""


class FakeArea(object):
    def __init__(self, script, bind_id):
        self._wheel_bind_id = bind_id
        self.tk = FakeTk(script)


class TestScrollAreaUnbind(unittest.TestCase):
    def test_destroy_removes_only_own_binding(self):
        own = 'if {"[mine %# %x]" == "break"} break\n'
        other = 'if {"[other %# %x]" == "break"} break\n'
        area = FakeArea(own + other, "mine")
        uikit.ScrollArea._on_destroy(area)
        writes = [c for c in area.tk.calls if len(c) == 4]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:3], ("bind", "all", "<MouseWheel>"))
        self.assertEqual(writes[0][3], other)

    def test_destroy_with_no_script_does_not_write(self):
        area = FakeArea("", "mine")
        uikit.ScrollArea._on_destroy(area)
        self.assertEqual([c for c in area.tk.calls if len(c) == 4], [])


if __name__ == "__main__":
    unittest.main()
