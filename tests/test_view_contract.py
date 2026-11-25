# -*- coding: utf-8 -*-
"""视图契约本身的可执行文档：FakeView 必须完整、可断言、无 tkinter。"""

import unittest

from brconsole.view_contract import (CALLBACKS, VARIABLES, FakeVar, FakeView,
                                     ViewProtocol)


class TestViewContract(unittest.TestCase):

    def test_fake_view_satisfies_protocol(self):
        self.assertIsInstance(FakeView(), ViewProtocol)

    def test_protocol_methods_all_implemented_by_fake(self):
        required = {name for name in dir(ViewProtocol)
                    if not name.startswith("_") and callable(getattr(ViewProtocol, name))}
        fake = FakeView()
        missing = sorted(name for name in required if not callable(getattr(fake, name, None)))
        self.assertEqual([], missing)

    def test_variables_are_complete_and_isolated(self):
        self.assertEqual(len(VARIABLES), len(set(VARIABLES)))
        view = FakeView()
        for name in VARIABLES:
            self.assertIn(name, view.vars)
        view.set("blend", "a.blend")
        view.set("scene", "SceneA")
        self.assertEqual("a.blend", view.get("blend"))
        self.assertEqual("SceneA", view.get("scene"))

    def test_fake_var_trace_fires_on_set(self):
        seen = []
        var = FakeVar(1)
        var.trace_add("write", lambda: seen.append(var.get()))
        var.set(2)
        self.assertEqual([2], seen)

    def test_set_text_and_configure_are_recorded(self):
        view = FakeView()
        view.set_text("status", "正在渲染", style="OK.TLabel")
        view.configure("depth", values=["8", "16"], state="readonly")
        kinds = [call[0] for call in view.calls]
        self.assertIn("set_text", kinds)
        self.assertIn("configure", kinds)
        self.assertEqual("正在渲染", view.text("status"))

    def test_queue_and_segments_roundtrip(self):
        view = FakeView()
        items = [{"id": "q1", "blend": "a.blend"}, {"id": "q2", "blend": "b.blend"}]
        view.set_queue(items, "q2")
        self.assertEqual((1,), view.queue_selection())
        segments = [{"start": "1", "end": "10", "step": "2"}]
        view.set_segments(segments)
        self.assertEqual(segments, view.read_segments())
        self.assertEqual(1, view.segment_count())

    def test_log_progress_and_modules(self):
        view = FakeView()
        view.append_log(["line1", "line2"])
        view.clear_log()
        view.set_progress(42.5)
        view.set_running(True)
        view.set_module_expanded("program", True)
        view.toggle_module("program")
        self.assertEqual([], view.log_lines)
        self.assertEqual(42.5, view.progress_value)
        self.assertTrue(view.running)
        self.assertFalse(view.modules["program"])

    def test_callback_names_are_semantic(self):
        expected = {"pick_blend", "on_queue_remove", "on_queue_move",
                    "on_queue_reorder", "on_start", "on_stop", "on_inspect",
                    "on_scene_change", "on_add_segment", "on_format_changed"}
        self.assertTrue(expected.issubset(set(CALLBACKS)))
        self.assertEqual(len(CALLBACKS), len(set(CALLBACKS)))


if __name__ == "__main__":
    unittest.main()
