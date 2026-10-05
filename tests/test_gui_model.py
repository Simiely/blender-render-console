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
from brconsole.core import parse_restart_limit  # noqa: E402
from brconsole.guimodel import (DEFAULT_RESTART_OPTION, RESTART_OPTIONS,  # noqa: E402
                                SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL,
                                FormModel, LogModel, ProgressModel, event_line,
                                fields_from_detail, frames_to_text, guess_state_path,
                                restart_label_for, scene_to_config, settle_task_action,
                                state_summary)
from brconsole.state import JobState  # noqa: E402


class TestRestartOptions(unittest.TestCase):
    """「崩溃后重启」下拉：文案是中文，且解析器认这些文案。

    历史 bug：界面用的是裸值列表（`1/3/5/10/unlimited`），下拉里直接显示出英文
    `unlimited`，而配套的中文文案从没被用上。
    """

    EXPECTED = {"1 次": 1, "3 次": 3, "5 次（默认）": 5, "10 次": 10,
                "一直重启，直到全部渲完": -1}

    def test_options_are_all_chinese_labels(self):
        self.assertEqual(set(RESTART_OPTIONS), set(self.EXPECTED))
        for label in RESTART_OPTIONS:
            self.assertNotIn("unlimited", label.lower(), label)

    def test_every_label_parses_to_expected_value(self):
        for label, want in self.EXPECTED.items():
            self.assertEqual(parse_restart_limit(label), want, label)

    def test_default_option_is_the_five_times_one(self):
        self.assertEqual(DEFAULT_RESTART_OPTION, "5 次（默认）")
        self.assertEqual(parse_restart_limit(DEFAULT_RESTART_OPTION), 5)


class TestSceneHelpers(unittest.TestCase):
    def test_placeholders_are_not_scene_names(self):
        """占位文案不能被当成场景名传给 Blender（否则会渲染到默认场景还报成功）。"""
        self.assertIsNone(scene_to_config(SCENE_DEFAULT_LABEL))
        self.assertIsNone(scene_to_config(SCENE_NEED_READ_LABEL))
        self.assertIsNone(scene_to_config(""))
        self.assertIsNone(scene_to_config(None))
        self.assertEqual(scene_to_config("Scene.001"), "Scene.001")
        self.assertEqual(scene_to_config("  洋房场景  "), "洋房场景")

    def test_fields_from_detail(self):
        detail = {
            "name": "SceneA", "engine": "cycles", "samples": 64,
            "resolution": [1920, 1080], "resolution_percentage": 50,
            "frame_start": 1, "frame_end": 24, "frame_step": 2,
            "file_format": "png", "cycles_device": "CPU", "output_path": "/tmp/",
        }
        f = fields_from_detail(detail, "D:/proj/house.blend")
        self.assertEqual(f["engine"], "CYCLES")          # 大写归一
        self.assertEqual(f["file_format"], "PNG")
        self.assertEqual(f["samples"], "64")
        self.assertEqual(f["device"], "CPU")
        self.assertEqual((f["width"], f["height"]), ("1920", "1080"))
        self.assertEqual(f["pct"], "50")
        self.assertEqual((f["start"], f["end"], f["step"]), ("1", "24", "2"))
        # Blender 默认的 /tmp/ 等于没设置 → 退回工程目录
        self.assertTrue(f["output"].replace("\\", "/").endswith("proj/house_####"), f)

    def test_fields_from_detail_skips_unknown_values(self):
        """拿不准的字段宁可不填，也不要写个错值进表单。"""
        f = fields_from_detail({"engine": "SOMETHING_NEW", "file_format": "AVI",
                                "cycles_device": "GPU", "compute_device_type": "METAL"},
                               "D:/a/b.blend")
        self.assertNotIn("engine", f)
        self.assertNotIn("file_format", f)
        self.assertNotIn("device", f)                    # METAL 不在我们支持的下拉里

    def test_fields_from_detail_empty(self):
        self.assertEqual(fields_from_detail(None), {})
        self.assertEqual(fields_from_detail({}, "D:/a/b.blend"), {})

    def test_form_passes_scene_to_config(self):
        d = tempfile.mkdtemp(prefix=".brc-gui-")
        try:
            blend = os.path.join(d, "s.blend")
            with open(blend, "wb") as f:
                f.write(b"BLENDER")
            cfg, errors, _ = FormModel(blend=blend, output=os.path.join(d, "o_####"),
                                       start="1", end="2",
                                       scene="Scene.002").to_config(parse_frames)
            self.assertEqual(errors, [])
            self.assertEqual(cfg.scene, "Scene.002")
            # 占位文案要走 None（= 用工程里激活的那个场景）
            cfg2, _, _ = FormModel(blend=blend, output=os.path.join(d, "o_####"),
                                   start="1", end="2",
                                   scene=SCENE_NEED_READ_LABEL).to_config(parse_frames)
            self.assertIsNone(cfg2.scene)
        finally:
            shutil.rmtree(d, ignore_errors=True)


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

    def test_negative_restarts_means_unlimited(self):
        """-1 / unlimited 是合法输入，表示「一直重启直到渲完」，不该报错。"""
        cfg, errors, _ = self._form(max_restarts="-1").to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertTrue(cfg.unlimited_restarts)

    def test_unlimited_word(self):
        cfg, errors, _ = self._form(max_restarts="unlimited").to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertTrue(cfg.unlimited_restarts)

    def test_no_progress_limit(self):
        cfg, errors, _ = self._form(max_no_progress="5").to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_no_progress_rounds, 5)

    def test_zero_frame_attempts_means_unlimited(self):
        cfg, errors, _ = self._form(max_frame_attempts="0").to_config(parse_frames)
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_frame_attempts, 0)

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


class TestFramesToText(unittest.TestCase):
    """帧号列表 → 表单文本（开机续跑回填用）。"""

    def test_contiguous_run_collapses(self):
        self.assertEqual(frames_to_text([1, 2, 3, 4]), "1-4")

    def test_short_run_stays_explicit(self):
        # 只有 2 帧的连续段不折成区间（"5-6" 没比 "5,6" 短，折了反而难读）
        self.assertEqual(frames_to_text([5, 6]), "5,6")

    def test_mixed(self):
        self.assertEqual(frames_to_text([1, 2, 3, 4, 9, 20, 21, 22]), "1-4,9,20-22")

    def test_unsorted_and_duplicated_input(self):
        self.assertEqual(frames_to_text([4, 2, 3, 2, 1]), "1-4")

    def test_empty(self):
        self.assertEqual(frames_to_text([]), "")
        self.assertEqual(frames_to_text(None), "")


class TestRestartLabelFor(unittest.TestCase):
    """存档里的整数 → 下拉文案。ROI 在于「填回去必须是能解析出同一个数」的文案。"""

    def test_known_values_map_to_canonical_labels(self):
        self.assertEqual(restart_label_for(1), RESTART_OPTIONS[0])
        self.assertEqual(restart_label_for(3), RESTART_OPTIONS[1])
        self.assertEqual(restart_label_for(5), RESTART_OPTIONS[2])
        self.assertEqual(restart_label_for(10), RESTART_OPTIONS[3])

    def test_unlimited(self):
        self.assertEqual(restart_label_for(-1), RESTART_OPTIONS[-1])
        self.assertEqual(restart_label_for(None), RESTART_OPTIONS[-1])

    def test_unknown_number_is_still_parseable(self):
        """不在候选里的次数（例如老存档写 7）不能静默退回 5 —— 必须原样带上。"""
        self.assertEqual(restart_label_for(7), "7 次")
        self.assertEqual(parse_restart_limit("7 次"), 7)

    def test_every_label_round_trips(self):
        for label in RESTART_OPTIONS:
            n = parse_restart_limit(label)
            self.assertEqual(restart_label_for(n), label)

    def test_default_option_is_used_when_missing(self):
        self.assertEqual(restart_label_for(5), DEFAULT_RESTART_OPTION)


class TestFormFromConfig(unittest.TestCase):
    """存档 → 表单 → 配置 的往返。这条链断了的表现是"开机续跑跑了另一个任务"。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix=".brc-fromcfg-")
        self.blend = os.path.join(self.d, "house.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER-v5")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _dict(self, **kw):
        from brconsole.core import JobConfig
        args = dict(blend=self.blend, frames=[1, 2, 3, 7, 8],
                    output_template=os.path.join(self.d, "out", "f_####"),
                    engine="CYCLES", samples=64, device="CPU", resolution=(1920, 1080),
                    resolution_percentage=50, file_format="PNG", scene="SceneB",
                    max_restarts=-1, max_frame_attempts=0, max_no_progress_rounds=7,
                    resume=False, restart_delay=1.5, extra_args=["-x"])
        args.update(kw)
        return JobConfig(**args).to_dict()

    def test_full_roundtrip(self):
        d = self._dict()
        cfg, errors, warns = FormModel.from_config(d, "C:/bl/blender.exe").to_config(
            parse_frames)
        self.assertEqual(errors, [])
        self.assertEqual(warns, [])
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.to_dict(), d)

    def test_frames_become_explicit_list_with_step_one(self):
        """区间展开要乘 step：回填时必须把 step 置回 1，否则 [1..8] 会变成 [1,3,5,7]。"""
        fm = FormModel.from_config(self._dict(frames=list(range(1, 9))))
        self.assertEqual(fm.step, "1")
        self.assertEqual(fm.frames, "1-8")

    def test_carries_fields_that_have_no_widget(self):
        """extra_args / restart_delay 界面上没有控件，但**必须原样带过去**：
        丢了它们，重建的任务会静默按默认值跑。"""
        d = self._dict(extra_args=["--cycles-device", "OPTIX"], restart_delay=0.25)
        fm = FormModel.from_config(d, "b.exe")
        self.assertEqual(fm.extra_args, ["--cycles-device", "OPTIX"])
        self.assertEqual(fm.restart_delay, 0.25)
        cfg, _, _ = fm.to_config(parse_frames)
        self.assertEqual(cfg.extra_args, ["--cycles-device", "OPTIX"])
        self.assertEqual(cfg.restart_delay, 0.25)

    def test_blank_optionals_become_keep(self):
        d = self._dict(engine=None, samples=None, device=None, file_format=None,
                       resolution=None, resolution_percentage=None, scene=None)
        fm = FormModel.from_config(d, "b.exe")
        self.assertEqual(fm.engine, "")
        self.assertEqual(fm.device, "")
        self.assertEqual(fm.file_format, "")
        self.assertEqual(fm.samples, "")
        self.assertEqual((fm.width, fm.height), ("", ""))
        self.assertEqual(fm.scene, SCENE_DEFAULT_LABEL)

    def test_blender_path_is_carried(self):
        fm = FormModel.from_config(self._dict(), r"D:\bl\blender.exe")
        self.assertEqual(fm.blender, r"D:\bl\blender.exe")

    def test_tolerates_empty_dict(self):
        fm = FormModel.from_config({}, "")
        self.assertEqual(fm.blend, "")
        self.assertEqual(fm.max_restarts, DEFAULT_RESTART_OPTION)


class TestSettleTaskAction(unittest.TestCase):
    """跑完后怎么处置待办存档 —— 这决定「开机要不要自动跑」，最不能出错的一环。

    核心分界：**崩溃**（进程被强杀/掉电，压根没机会写文件）→ 开机自动续跑；
    **活着的进程报出来的失败**（取消 / 重试额度耗尽 / 抛异常）→ 别自动跑，
    否则一个注定失败的任务会每次开机白跑一遍。所以这里看的是"谁报的结果"，不是"成没成功"。
    """

    def test_complete_clears_the_archive(self):
        self.assertEqual(settle_task_action("job_done", {"ok": True}), ("clear", ""))

    def test_cancelled_stops_authorun_but_keeps_reason(self):
        action, reason = settle_task_action("job_done", {"ok": False, "cancelled": True})
        self.assertEqual(action, "stop")
        self.assertIn("停止", reason)

    def test_restart_exhausted_does_not_rerun_at_boot(self):
        """重启额度用完 ⇒ 下次开机别再自动跑（断点还在，用户点一下就能继续）。"""
        action, reason = settle_task_action(
            "job_done", {"ok": False, "cancelled": False, "error": None})
        self.assertEqual(action, "stop")
        self.assertIn("没能跑完", reason)

    def test_error_is_reported_with_its_message(self):
        action, reason = settle_task_action("job_error", {"error": "Blender 崩了"})
        self.assertEqual(action, "stop")
        self.assertIn("Blender 崩了", reason)

    def test_partial_ok_is_not_complete(self):
        """`ok=False` 但零失败（例如被取消）绝不能当成「渲完了」去删档。"""
        self.assertEqual(settle_task_action("job_done", {"ok": False})[0], "stop")

    def test_broken_event_does_not_crash(self):
        for kind, ev in (("job_done", {}), ("job_error", {}),
                         ("job_done", {"ok": None, "cancelled": None})):
            action, reason = settle_task_action(kind, ev)
            self.assertEqual(action, "stop", (kind, ev))
            self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
