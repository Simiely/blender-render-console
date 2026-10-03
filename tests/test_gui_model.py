# -*- coding: utf-8 -*-
"""GUI 逻辑层测试（不启动 tkinter —— 这也是把逻辑抽到 guimodel 的原因）。

覆盖：表单校验与补全、事件→界面字段的折算、日志缓冲、tkinter 自举环境变量。
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from brconsole import tkboot  # noqa: E402
from brconsole.cli import parse_frames  # noqa: E402
from brconsole.guimodel import (FormModel, LogModel, ProgressModel, event_line,  # noqa: E402
                                guess_state_path, state_summary)
from brconsole.state import JobState  # noqa: E402


class TestCompleteOutput(unittest.TestCase):
    def test_dir_gets_template(self):
        self.assertTrue(FormModel.complete_output("D:/out").replace("\\", "/")
                        .endswith("out/frame_####"))

    def test_file_gets_hash_placeholder(self):
        out = FormModel.complete_output("D:/out/scene.png")
        self.assertTrue(out.endswith("scene_####.png"), out)

    def test_existing_placeholder_kept(self):
        self.assertEqual(FormModel.complete_output("D:/out/f_####"), "D:/out/f_####")

    def test_empty_with_blend(self):
        out = FormModel.complete_output("", "D:/proj/scene.blend")
        self.assertTrue(out.replace("\\", "/").endswith("proj/scene_####"), out)

    def test_empty_without_blend(self):
        self.assertEqual(FormModel.complete_output(""), "")


class TestFormValidation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=".brc-gui-")
        self.blend = os.path.join(self.dir, "scene.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER")
        self.out = os.path.join(self.dir, "out", "f_####")

    def tearDown(self):
        try:
            shutil.rmtree(self.dir)
        except Exception:
            pass

    def _form(self, **kw):
        base = dict(blend=self.blend, output=self.out, start="1", end="4")
        base.update(kw)
        return FormModel(**base)

    def test_ok(self):
        cfg, errors, warns = self._form().to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertEqual(cfg.frames, [1, 2, 3, 4])
        self.assertEqual(cfg.output_template, self.out)
        self.assertIsNone(cfg.engine)          # KEEP 要翻译成 None（保持工程设置）

    def test_missing_blend(self):
        cfg, errors, _ = self._form(blend="").to_config(parse_frames)
        self.assertIsNone(cfg)
        self.assertTrue(any("工程文件" in e for e in errors))

    def test_blend_not_exists(self):
        cfg, errors, _ = self._form(blend="D:/nope.blend").to_config(parse_frames)
        self.assertIsNone(cfg)
        self.assertTrue(any("不存在" in e for e in errors))

    def test_no_frames(self):
        cfg, errors, _ = self._form(start="", end="", frames="").to_config(parse_frames)
        self.assertIsNone(cfg)
        self.assertTrue(any("帧" in e for e in errors))

    def test_explicit_frames_win(self):
        cfg, _, _ = self._form(frames="2,5-7").to_config(parse_frames)
        self.assertEqual(cfg.frames, [2, 5, 6, 7])

    def test_half_resolution_rejected(self):
        cfg, errors, _ = self._form(width="1920").to_config(parse_frames)
        self.assertIsNone(cfg)
        self.assertTrue(any("宽高" in e for e in errors))

    def test_bad_int(self):
        cfg, errors, _ = self._form(samples="很多").to_config(parse_frames)
        self.assertIsNone(cfg)
        self.assertTrue(any("采样" in e for e in errors))

    def test_negative_restarts(self):
        cfg, errors, _ = self._form(max_restarts="-1").to_config(parse_frames)
        self.assertIsNone(cfg)
        self.assertTrue(any("不能是负数" in e for e in errors))

    def test_numeric_options(self):
        cfg, errors, _ = self._form(samples="128", pct="50", width="1920",
                                    height="1080", engine="CYCLES",
                                    device="OPTIX", resume=False).to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertEqual(cfg.samples, 128)
        self.assertEqual(cfg.resolution_percentage, 50)
        self.assertEqual(cfg.resolution, (1920, 1080))
        self.assertEqual(cfg.engine, "CYCLES")
        self.assertEqual(cfg.device, "OPTIX")
        self.assertFalse(cfg.resume)

    def test_unusual_extension_warns(self):
        weird = os.path.join(self.dir, "scene.txt")
        with open(weird, "wb") as f:
            f.write(b"x")
        _, errors, warns = self._form(blend=weird).to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertTrue(any("扩展名" in w for w in warns))


class TestProgressModel(unittest.TestCase):
    def test_happy_path(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 6, "blend": "a.blend"})
        self.assertTrue(p.running)
        p.on_event("frame_start", {"frame": 1})
        self.assertEqual(p.current_frame, 1)
        p.on_event("frame_done", {"frame": 1, "done": 1, "total": 6, "secs": 3.0,
                                  "eta_sec": None, "eta_mode": "尚无样本", "warmup": True})
        self.assertEqual(p.done, 1)
        self.assertIn("1/6", p.status_text())     # 帧完成时状态行要报进度
        p.on_event("frame_start", {"frame": 2})
        self.assertIn("正在渲染帧 2", p.status_text())
        p.on_event("frame_done", {"frame": 2, "done": 2, "total": 6, "secs": 1.0,
                                  "per_frame": 1.0, "eta_sec": 4.0, "eta_mode": "EMA"})
        self.assertAlmostEqual(p.fraction, 2 / 6)
        self.assertIn("ETA", p.eta_text())
        p.on_event("job_done", {"done": [1, 2, 3, 4, 5, 6], "total": 6, "ok": True,
                                "elapsed": 12.0, "restarts": 0, "failed": {},
                                "exhausted": []})
        self.assertTrue(p.finished)
        self.assertTrue(p.ok)
        self.assertIn("全部完成", p.status_text())
        self.assertEqual(p.eta_text(), "")

    def test_phase_shown_when_no_percentage(self):
        """大场景同步阶段没有百分比可显示 —— 界面必须显示阶段文案，不能空白。"""
        p = ProgressModel()
        p.on_event("job_start", {"total": 100})
        p.on_event("native", {"phase": "正在构建 BVH"})
        self.assertIn("正在构建 BVH", p.status_text())

    def test_stall_is_explained(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 100})
        p.on_event("stall", {"idle_sec": 62.0, "phase": "正在编译着色器"})
        self.assertTrue(p.stalled)
        self.assertIn("没有新输出", p.status_text())

    def test_cancel_and_error(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 4})
        p.on_event("job_done", {"done": [1, 2], "total": 4, "cancelled": True,
                                "elapsed": 5.0, "ok": False, "failed": {},
                                "exhausted": [], "restarts": 0})
        self.assertIn("已取消", p.status_text())

        p2 = ProgressModel()
        p2.on_event("job_start", {"total": 4})
        p2.on_event("job_error", {"error": "找不到 Blender"})
        self.assertTrue(p2.finished)
        self.assertEqual(p2.error, "找不到 Blender")

    def test_crash_counting(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 6})
        p.on_event("crash", {"rc": 1, "restart_index": 1, "done": 2, "total": 6})
        p.on_event("restart", {"restart_index": 1, "pending": [3, 4, 5, 6]})
        self.assertEqual(p.crashes, 1)
        self.assertEqual(p.restarts, 1)
        self.assertIn("重启", p.last_message)

    def test_fraction_bounded(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 0})
        self.assertEqual(p.fraction, 0.0)


class TestLogModel(unittest.TestCase):
    def test_drain_and_clear(self):
        m = LogModel()
        m.add("第一行")
        m.add("第二行\r第三行")
        self.assertEqual(len(m), 3)
        got = m.drain()
        self.assertEqual(len(got), 3)
        self.assertEqual(len(m), 0)
        self.assertEqual(m.total, 3)

    def test_blank_lines_dropped(self):
        m = LogModel()
        m.add("   \n\n真实内容\n")
        self.assertEqual(m.drain(), ["真实内容"])

    def test_limit(self):
        m = LogModel()
        for i in range(20):
            m.add(str(i))
        self.assertEqual(len(m.drain(limit=5)), 5)
        self.assertEqual(len(m), 15)


class TestEventLine(unittest.TestCase):
    def test_native_not_logged(self):
        self.assertIsNone(event_line("native", {"line": "Fra: 1"}))

    def test_frame_done_has_eta(self):
        line = event_line("frame_done", {"frame": 3, "secs": 1.5, "done": 3, "total": 6,
                                         "eta_sec": 4.5, "remaining": 3})
        self.assertIn("帧 3 完成", line)
        self.assertIn("ETA", line)

    def test_warmup_marked(self):
        line = event_line("frame_done", {"frame": 1, "secs": 10.0, "done": 1, "total": 6,
                                         "eta_sec": None, "warmup": True})
        self.assertIn("预热帧", line)

    def test_crash_line(self):
        self.assertIn("退出", event_line("crash", {"rc": 1, "done": 2, "total": 6,
                                                   "restart_index": 1}))


class TestStateHelpers(unittest.TestCase):
    def test_guess_state_path(self):
        p = guess_state_path("D:/out/f_####")
        self.assertEqual(os.path.basename(p), ".render_state.json")

    def test_state_summary(self):
        d = tempfile.mkdtemp(prefix=".brc-gui-")
        try:
            p = os.path.join(d, ".render_state.json")
            st = JobState(p, frames=[1, 2, 3])
            st.mark_done(1, secs=1.0)
            st.save()
            info = state_summary(p)
            self.assertEqual(info["done"], 1)
            self.assertEqual(info["total"], 3)
            self.assertIsNone(state_summary(os.path.join(d, "nope.json")))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestTkBoot(unittest.TestCase):
    def test_no_sidecar(self):
        self.assertIsNone(tkboot.sidecar_env(tempfile.gettempdir()))

    def test_env_built(self):
        d = tempfile.mkdtemp(prefix=".brc-gui-")
        try:
            side = os.path.join(d, "sidecar")
            for sub in ("DLLs", "Lib"):
                os.makedirs(os.path.join(side, sub))
            env = tkboot.sidecar_env(d, base={"PATH": "X", "PYTHONPATH": ""})
            self.assertIsNotNone(env)
            self.assertTrue(env["PYTHONPATH"].startswith(
                os.path.join(side, "Lib")))
            self.assertTrue(env["PATH"].startswith(os.path.join(side, "DLLs")))
            self.assertEqual(env["TCL_LIBRARY"], os.path.join(side, "tcl", "tcl8.6"))
            self.assertEqual(env["TK_LIBRARY"], os.path.join(side, "tcl", "tk8.6"))
            self.assertEqual(env[tkboot.BOOT_FLAG], "1")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
