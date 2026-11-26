# -*- coding: utf-8 -*-
"""min_gui 纯逻辑的契约测试（不依赖 tkinter）。"""

import os
import tempfile
import unittest

from brconsole import queue_store
from brconsole.guimodel.constants import DEPTH_KEEP_LABEL, FORMAT_KEEP_LABEL
from min_gui_model import build_config, fill_from_detail, queue_status_after


class TestBuildConfig(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="min-gui-model-")
        self.blend = os.path.join(self.d, "p.blend")
        with open(self.blend, "wb") as fp:
            fp.write(b"BLENDER-v5")
        self.blender = os.path.join(self.d, "blender.exe")
        with open(self.blender, "wb") as fp:
            fp.write(b"MZ")
        self.base = {
            "blend": self.blend, "blender": self.blender, "scene": "Scene",
            "frames_spec": "", "start": "1", "end": "3",
            "engine": "CYCLES", "device": "CPU", "samples": "16",
            "width": "160", "height": "120", "pct": "100",
            "format": "PNG", "depth": "8", "output": os.path.join(self.d, "out", "f_####"),
            "retry": "3 次", "resume": True, "hidden": ["Secret"],
            "restarts_override": "", "attempts_override": "", "no_progress_override": "",
        }

    def test_full_fields_reach_config(self):
        cfg, err = build_config(self.base)
        self.assertEqual(err, "")
        self.assertEqual(cfg.frames, [1, 2, 3])
        self.assertEqual((cfg.samples, cfg.device, cfg.engine), (16, "CPU", "CYCLES"))
        self.assertEqual(cfg.resolution, (160, 120))
        self.assertEqual(cfg.resolution_percentage, 100)
        self.assertEqual(cfg.file_format, "PNG")
        self.assertEqual(cfg.color_depth, "8")
        self.assertEqual(cfg.scene, "Scene")
        self.assertEqual(cfg.hidden_collections, ["Secret"])
        self.assertEqual((cfg.max_restarts, cfg.max_frame_attempts,
                          cfg.max_no_progress_rounds), (3, 3, 3))

    def test_frames_spec_overrides_range_and_step(self):
        v = dict(self.base, frames_spec="1-5x2,9")
        cfg, err = build_config(v)
        self.assertEqual(err, "")
        self.assertEqual(cfg.frames, [1, 3, 5, 9])

    def test_bad_frames_spec_reports_segment(self):
        v = dict(self.base, frames_spec="1,xx")
        cfg, err = build_config(v)
        self.assertIsNone(cfg)
        self.assertIn("第 2 段", err)

    def test_keep_format_and_depth_are_none(self):
        v = dict(self.base, format=FORMAT_KEEP_LABEL, depth=DEPTH_KEEP_LABEL)
        cfg, err = build_config(v)
        self.assertEqual(err, "")
        self.assertIsNone(cfg.file_format)
        self.assertIsNone(cfg.color_depth)

    def test_overrides_win_over_retry_dropdown(self):
        v = dict(self.base, restarts_override="7", attempts_override="0",
                 no_progress_override="5")
        cfg, err = build_config(v)
        self.assertEqual((cfg.max_restarts, cfg.max_frame_attempts,
                          cfg.max_no_progress_rounds), (7, 0, 5))

    def test_missing_blend_reports_error(self):
        v = dict(self.base, blend="")
        cfg, err = build_config(v)
        self.assertIsNone(cfg)
        self.assertIn(".blend", err)


class TestFillFromDetail(unittest.TestCase):
    def test_all_fields_filled(self):
        detail = {
            "engine": "BLENDER_EEVEE_NEXT", "samples": 24, "cycles_device": "CPU",
            "resolution": [320, 240], "resolution_percentage": 50,
            "frame_start": 7, "frame_end": 11,
            "file_format": "PNG", "color_depth": "16", "output_path": "//renders/f_####",
        }
        v = fill_from_detail({}, detail, r"D:\p\scene.blend")
        self.assertEqual(v["engine"], "BLENDER_EEVEE")   # 4.x 别名归一
        self.assertEqual(v["samples"], "24")
        self.assertEqual(v["device"], "CPU")
        self.assertEqual((v["width"], v["height"], v["pct"]), ("320", "240", "50"))
        self.assertEqual((v["start"], v["end"]), ("7", "11"))
        self.assertEqual(v["format"], "PNG")
        self.assertEqual(v["depth"], "16")

    def test_gpu_backend_filled_from_compute_device_type(self):
        # cycles_device 只有 CPU/GPU 两值；GPU 时真正的后端名在 compute_device_type
        detail = {"engine": "CYCLES", "cycles_device": "GPU",
                  "compute_device_type": "OPTIX"}
        v = fill_from_detail({}, detail)
        self.assertEqual(v["device"], "OPTIX")

    def test_cpu_device_filled_from_cycles_device(self):
        v = fill_from_detail({}, {"engine": "CYCLES", "cycles_device": "CPU"})
        self.assertEqual(v["device"], "CPU")

    def test_unknown_gpu_backend_does_not_guess(self):
        # 读不到后端名时不动控件（宁缺勿错），保持原值
        v = fill_from_detail({"device": ""}, {"engine": "CYCLES", "cycles_device": "GPU"})
        self.assertEqual(v["device"], "")

    def test_unknown_fields_leave_values_untouched(self):
        v = fill_from_detail({"samples": "99"}, {"samples": None})
        self.assertEqual(v["samples"], "99")


class TestQueueStatusAfter(unittest.TestCase):
    def test_done_when_all_frames_rendered(self):
        self.assertEqual(queue_status_after("job_done", {"ok": True}),
                         queue_store.STATUS_DONE)

    def test_pending_when_user_cancelled(self):
        self.assertEqual(queue_status_after("job_done", {"ok": False, "cancelled": True}),
                         queue_store.STATUS_PENDING)

    def test_failed_when_frames_exhausted(self):
        self.assertEqual(queue_status_after("job_done", {"ok": False, "cancelled": False}),
                         queue_store.STATUS_FAILED)

    def test_job_error_is_failed(self):
        self.assertEqual(queue_status_after("job_error", {"error": "boom"}),
                         queue_store.STATUS_FAILED)


if __name__ == "__main__":
    unittest.main()
