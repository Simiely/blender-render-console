# -*- coding: utf-8 -*-
"""控制器的特性测试：用 FakeView 锁住"输入什么 → 调了什么语义方法"。

这些测试不构造 Tk 窗口，也不依赖 Blender；目标是给后续把旧控件别名
迁移到 `ViewProtocol` 时提供行为护栏。
"""

import os
import queue
import tempfile
import unittest
from unittest import mock

from brconsole.gui.form_controller import FormControllerMixin
from brconsole.gui.inspect_controller import InspectControllerMixin
from brconsole.gui.queue_controller import QueueControllerMixin
from brconsole.gui.render_controller import RenderControllerMixin
from brconsole.guimodel import FormModel, LogModel, ProgressModel
from brconsole.view_contract import FakeView


_CONTROL_NAMES = {
    "lbl_retry_note": "retry_note", "lbl_autostart": "autostart_note",
    "lbl_scene": "scene_note", "lbl_frames": "frames_preview",
    "lbl_inspect": "inspect_note", "lbl_collections": "collection_status",
    "lbl_queue_count": "queue_count", "lbl_queue": "queue",
    "btn_start": "start", "btn_stop": "stop",
    "btn_inspect": "inspect", "btn_clear_state": "clear_state",
    "cmb_depth": "depth", "cmb_scene": "scene",
    "cmb_retry": "retry", "cmb_blender_ver": "blender_ver",
}


class FakeControl(object):
    """旧控件 API 的测试替身：configure/cget 落到 FakeView。"""

    def __init__(self, view, name):
        self.view = view
        self.name = _CONTROL_NAMES.get(name, name)

    def configure(self, **kw):
        if "text" in kw:
            self.view.set_text(self.name, kw["text"], **{k: v for k, v in kw.items() if k != "text"})
        else:
            self.view.configure(self.name, **kw)

    def cget(self, key):
        if key == "text":
            return self.view.text(self.name)
        if key == "font":
            return ("Microsoft YaHei", 9)
        return ""

    def __setitem__(self, key, value):
        self.view.configure(self.name, **{key: value})


class FakeRoot(object):
    def after(self, _ms, _cb):
        return "after-id"

    def destroy(self):
        self.destroyed = True


class Harness(FormControllerMixin, InspectControllerMixin,
              QueueControllerMixin, RenderControllerMixin):
    def __init__(self):
        object.__setattr__(self, "view", FakeView())
        self.form = FormModel()
        self.prog = ProgressModel()
        self.log = LogModel()
        self.root = FakeRoot()
        self.demo = False
        self.cmd_factory = None
        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.stopping = False
        self._logs = []
        self._blender_by_label = {}
        self._blender_candidates = []
        self._selected_queue_id = None
        self._inspected_path = None
        self._inspecting = False
        self._inspect_token = 0
        self._inspect_path = None
        self._inspect_item_id = None
        self._scene_info = None
        self._scene_info_cache = {}
        self.hidden_collections = []
        self._collection_vars = {}
        self._collection_objects = {}
        self._seg_rows = []
        self._frame_preview = []
        self._overview_tags = {}
        self._queue_refreshing = False
        self._queue_mode = False
        self._queue_total = 0
        self._queue_index = 0
        self._last_queue_event = None
        self._refresh_calls = 0

    def __getattr__(self, name):
        if name.startswith("v_"):
            return self.view.var({"v_w": "width", "v_h": "height", "v_pct": "pct",
                                  "v_format": "format", "v_depth": "depth",
                                  "v_blender_ver": "blender_ver"}.get(name, name[2:]))
        if name.startswith(("lbl_", "cmb_", "chk_", "btn_")):
            return FakeControl(self.view, name)
        raise AttributeError(name)

    def _log(self, text):
        self._logs.append(text)

    def _read_segments(self):
        return self.view.read_segments()

    def _refresh_queue(self):
        self._refresh_calls += 1

    def _rebuild_collection_ui(self, *_a, **_k):
        return

    def _sync_overview(self):
        return

    def _refresh_state_label(self):
        return


class TestFormController(unittest.TestCase):

    def setUp(self):
        self.h = Harness()
        self.h.view.set_segments([{"start": "1", "end": "10", "step": "2"}])

    def test_collect_form_reads_all_view_vars(self):
        v = self.h.view
        for key, value in (("blend", "a.blend"), ("scene", "SceneA"),
                           ("blender", "blender.exe"), ("output", "out_####"),
                           ("engine", "CYCLES"), ("samples", "32"),
                           ("device", "CPU"), ("width", "1920"),
                           ("height", "1080"), ("pct", "100"),
                           ("format", "PNG"), ("depth", "16"),
                           ("resume", False), ("retry", "3 次")):
            v.set(key, value)
        self.h._collect_form()
        f = self.h.form
        self.assertEqual("a.blend", f.blend)
        self.assertEqual("SceneA", f.scene)
        self.assertEqual("out_####", f.output)
        self.assertEqual("CYCLES", f.engine)
        self.assertEqual("32", f.samples)
        self.assertEqual("CPU", f.device)
        self.assertEqual("1920", f.width)
        self.assertEqual("1080", f.height)
        self.assertEqual("100", f.pct)
        self.assertEqual("PNG", f.file_format)
        self.assertEqual("16", f.color_depth)
        self.assertFalse(f.resume)
        self.assertEqual("3 次", f.retry)

    def test_retry_note_updates_semantic_text(self):
        self.h.view.set("retry", "3 次")
        self.h._sync_retry_note()
        self.assertIn("重启与单帧都按 3 次", self.h.view.text("retry_note"))
        self.assertIn("连续 3 轮", self.h.view.text("retry_note"))

    def test_depth_ui_sets_values_and_depth(self):
        changed, was_changed = self.h._sync_depth_ui("PNG", "32")
        self.assertEqual("8", changed)
        self.assertTrue(was_changed)
        self.assertEqual("8", self.h.view.get("depth"))
        self.assertTrue(any(call[0] == "configure" and call[1][0] == "depth"
                            and call[2].get("values") == ["8", "16"]
                            for call in self.h.view.calls))

    def test_format_change_retargets_extension(self):
        self.h.view.set("format", "OPEN_EXR")
        self.h.view.set("output", "f_####.png")
        self.h.view.set("depth", "8")
        self.h._on_format_changed()
        self.assertEqual("f_####.exr", self.h.view.get("output"))
        self.assertEqual("32", self.h.view.get("depth"))


class TestInspectController(unittest.TestCase):

    def setUp(self):
        self.h = Harness()

    def test_plausible_blender(self):
        self.assertTrue(self.h._plausible_blender(r"C:\Program Files\Blender\blender.exe"))
        self.assertFalse(self.h._plausible_blender(r"C:\Python\python.exe"))

    def test_sync_versions_and_select_path(self):
        self.h._blender_candidates = [r"C:\B\5.2\blender.exe", r"C:\B\4.5\blender.exe"]
        self.h._sync_blender_versions()
        self.assertEqual("5.2", self.h.view.get("blender_ver"))
        self.h.view.set("blender_ver", "4.5")
        self.h.on_blender_version()
        self.assertEqual(r"C:\B\4.5\blender.exe", self.h.view.get("blender"))
        self.assertTrue(any("Blender 改为" in line for line in self.h._logs))

    def test_blender_var_change_clears_unknown_version(self):
        self.h._blender_by_label = {"5.2": r"C:\B\5.2\blender.exe"}
        self.h.view.set("blender_ver", "5.2")
        self.h.view.set("blender", r"C:\Other\blender.exe")
        self.h._on_blender_var_changed()
        self.assertEqual("", self.h.view.get("blender_ver"))

    def test_scene_combo_and_hint(self):
        self.h._update_scene_combo(["SceneA", "SceneB"], "SceneB")
        self.assertEqual("SceneB", self.h.view.get("scene"))
        self.assertIn("2 个场景", self.h.view.text("scene_note"))
        self.assertTrue(any("工程里有 2 个场景" in line for line in self.h.log.drain()))


class TestQueueController(unittest.TestCase):

    def setUp(self):
        self.h = Harness()

    def test_short_choice_and_path(self):
        choices = [("保持工程设置", ""), ("Cycles", "CYCLES")]
        self.assertEqual("随工程", self.h._short_choice(choices, ""))
        self.assertEqual("Cycles", self.h._short_choice(choices, "CYCLES"))
        self.assertEqual("…\\out\\f_####", self.h._short_path(r"C:\work\out\f_####"))

    def test_selected_item_falls_back_to_first(self):
        q = {"items": [{"id": "a"}, {"id": "b"}]}
        with mock.patch("brconsole.gui.queue_controller.queue_store.load",
                        return_value=q):
            self.h._selected_queue_id = "missing"
            self.assertEqual("a", self.h._selected_item()["id"])

    def test_apply_queue_item_uses_shared_form_model(self):
        item = {"id": "q1", "config": {"blend": "a.blend", "frames": [1, 2],
                                       "output_template": "out_####"},
                "blender_exe": "blender.exe"}
        self.h._apply_form_model = mock.Mock()
        self.h._apply_queue_item(item)
        fm = self.h._apply_form_model.call_args.args[0]
        self.assertIsInstance(fm, FormModel)
        self.assertEqual("a.blend", fm.blend)


class TestRenderController(unittest.TestCase):

    def setUp(self):
        self.h = Harness()

    def test_scene_conflict_blocks_unknown_scene(self):
        self.h.view.set("blend", os.path.abspath("a.blend"))
        self.h.view.set("scene", "NoSuchScene")
        self.h._scene_info = {"ok": True, "blend": os.path.abspath("a.blend"),
                              "scene": "SceneA", "scenes": ["SceneA", "SceneB"]}
        msg = self.h._scene_conflict()
        self.assertIn("没有场景", msg)
        self.assertIn("SceneA", msg)

    def test_scene_conflict_allows_known_scene(self):
        self.h.view.set("blend", os.path.abspath("a.blend"))
        self.h.view.set("scene", "SceneB")
        self.h._scene_info = {"ok": True, "blend": os.path.abspath("a.blend"),
                              "scene": "SceneA", "scenes": ["SceneA", "SceneB"]}
        self.assertEqual("", self.h._scene_conflict())

    def test_on_stop_cancels_and_disables_stop(self):
        class Job(object):
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        job = Job()
        self.h.job = job
        with mock.patch("brconsole.gui.render_controller.taskstore.set_autoresume",
                        return_value=True):
            self.h.on_stop()
        self.assertTrue(job.cancelled)
        self.assertTrue(self.h.stopping)
        self.assertTrue(any(call[0] == "configure" and call[1][0] == "stop"
                            and call[2].get("state") == "disabled"
                            for call in self.h.view.calls))
        self.assertTrue(any("正在停止" in line for line in self.h._logs))


if __name__ == "__main__":
    unittest.main()
