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
from brconsole.core import UNLIMITED, parse_restart_limit  # noqa: E402
from brconsole.guimodel import (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN,  # noqa: E402
                                AFTERMATH_SLEEP, DEFAULT_ATTEMPTS,
                                DEFAULT_NO_PROGRESS, DEFAULT_RESTART_OPTION,
                                RESTART_OPTIONS,
                                SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL,
                                UNLIMITED_LIMITS, UNLIMITED_RESTART_LABEL, FormModel,
                                LogModel, NativeLineFilter, ProgressModel,
                                aftermath_plan, event_line,
                                fields_from_detail, frames_preview, frames_to_text,
                                frames_to_segments, guess_state_path, new_segment,
                                segment_label, segments_to_frames,
                                is_unlimited_restart, restart_label_for,
                                restart_limits_plan, restore_limits_backup,
                                scene_to_config, settle_task_action, state_summary,
                                unlimited_limit_fill)
from brconsole.state import JobState  # noqa: E402


class TestRestartOptions(unittest.TestCase):
    """「崩溃后重启」下拉：文案是中文，且解析器认这些文案。

    历史 bug：界面用的是裸值列表（`1/3/5/10/unlimited`），下拉里直接显示出英文
    `unlimited`，而配套的中文文案从没被用上。
    """

    EXPECTED = {"1 次": 1, "3 次": 3, "5 次": 5, "10 次": 10,
                "一直重启，直到全部渲完（默认）": -1}

    def test_options_are_all_chinese_labels(self):
        self.assertEqual(set(RESTART_OPTIONS), set(self.EXPECTED))
        for label in RESTART_OPTIONS:
            self.assertNotIn("unlimited", label.lower(), label)

    def test_every_label_parses_to_expected_value(self):
        for label, want in self.EXPECTED.items():
            self.assertEqual(parse_restart_limit(label), want, label)

    def test_default_option_is_unlimited(self):
        """v0.8.1 起默认「一直重启」：崩溃续跑是本程序的卖点，
        有限次数会让长任务在"其实还能接着跑"的时候提前放弃。"""
        self.assertEqual(DEFAULT_RESTART_OPTION, "一直重启，直到全部渲完（默认）")
        self.assertEqual(parse_restart_limit(DEFAULT_RESTART_OPTION), UNLIMITED)
        # 默认项必须排在候选里（readonly 下拉只会显示候选里的文案）
        self.assertIn(DEFAULT_RESTART_OPTION, RESTART_OPTIONS)


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
                                       segments=[new_segment("1", "2")],
                                       scene="Scene.002").to_config()
            self.assertEqual(errors, [])
            self.assertEqual(cfg.scene, "Scene.002")
            # 占位文案要走 None（= 用工程里激活的那个场景）
            cfg2, _, _ = FormModel(blend=blend, output=os.path.join(d, "o_####"),
                                   segments=[new_segment("1", "2")],
                                   scene=SCENE_NEED_READ_LABEL).to_config()
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

    def test_cli_and_gui_share_one_rule(self):
        """★ CLI 与 GUI 必须是**同一份**规则 —— 原先各写一遍且语义不同
        （`-o render`：CLI 当文件名前缀、GUI 当目录，同一个输入两种落点）。
        钉住"委托"这件事本身：哪边想把规则再抄一份，这条就红。
        """
        import brconsole.cli as cli
        from brconsole.core import complete_output_template
        self.assertIs(cli.complete_output_template, complete_output_template)
        for path, blend in (("D:/out", ""), ("D:/out/scene.png", ""),
                            ("D:/out/f_####", ""), ("", "D:/proj/scene.blend"),
                            ("D:/renders/render", "")):
            self.assertEqual(FormModel.complete_output(path, blend),
                             complete_output_template(path, blend), path)


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
        base = dict(blend=self.blend, output=self.out,
                    segments=[new_segment("1", "4")])
        base.update(kw)
        return FormModel(**base)

    def test_ok(self):
        cfg, errors, warns = self._form().to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.frames, [1, 2, 3, 4])
        self.assertEqual(cfg.output_template, self.out)
        self.assertIsNone(cfg.engine)          # KEEP 要翻译成 None（保持工程设置）

    # ---- 三个「不限」同时成立 = 没有任何终止条件（P-W4，走查捞出）----
    def _triple_unlimited(self, **kw):
        base = dict(max_restarts=UNLIMITED_RESTART_LABEL,
                    max_frame_attempts="0", max_no_progress="0")
        base.update(kw)
        return self._form(**base).to_config()

    def test_triple_unlimited_warns(self):
        cfg, errors, warns = self._triple_unlimited()
        self.assertEqual(errors, [])
        self.assertIsNotNone(cfg)              # 不硬拦：用户确实可能想"跑到通为止"
        self.assertTrue(any("三个都设成了不限" in w for w in warns), warns)
        self.assertEqual((cfg.max_frame_attempts, cfg.max_no_progress_rounds), (0, 0))

    def test_unlimited_restarts_alone_keeps_the_guard_quiet(self):
        """只选「一直重启」时不该报警 —— 默认填充本来就是 0/3，兜底还在。"""
        cfg, errors, warns = self._form(
            max_restarts=UNLIMITED_RESTART_LABEL,
            max_frame_attempts="0", max_no_progress="3").to_config()
        self.assertEqual(errors, [])
        self.assertFalse([w for w in warns if "三个都设成了不限" in w], warns)

    def test_finite_restarts_never_warn_about_unlimited(self):
        cfg, _errors, warns = self._form(
            max_restarts="5 次", max_frame_attempts="0", max_no_progress="0").to_config()
        self.assertFalse([w for w in warns if "三个都设成了不限" in w], warns)

    def test_missing_blend(self):
        cfg, errors, _ = self._form(blend="").to_config()
        self.assertIsNone(cfg)
        self.assertTrue(any("工程文件" in e for e in errors))

    def test_blend_not_exists(self):
        cfg, errors, _ = self._form(blend="D:/nope.blend").to_config()
        self.assertIsNone(cfg)
        self.assertTrue(any("不存在" in e for e in errors))

    def test_no_frames(self):
        cfg, errors, _ = self._form(segments=[new_segment("", "")]).to_config()
        self.assertIsNone(cfg)
        self.assertTrue(any("帧" in e for e in errors))

    def test_multiple_discontinuous_segments(self):
        """★ 多段不连续序列帧 —— 本次新增能力的内核：段列表直接展开成帧号，
        每段自带步长，段之间重复的帧去重。"""
        cfg, errors, _ = self._form(segments=[
            new_segment("1", "10"),          # 每 1 帧
            new_segment("15"),               # 单帧（只填起帧）
            new_segment("20", "30", "5"),    # 每 5 帧
        ]).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.frames, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30])

    def test_segment_overlap_is_deduped(self):
        cfg, errors, _ = self._form(segments=[new_segment("1", "5"),
                                              new_segment("3", "8")]).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.frames, [1, 2, 3, 4, 5, 6, 7, 8])

    def test_start_bigger_than_end_is_swapped(self):
        cfg, errors, _ = self._form(segments=[new_segment("9", "7")]).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.frames, [7, 8, 9])

    def test_blank_segment_is_skipped(self):
        """刚点「添加一段」还没填的空行不该拦住整个任务。"""
        cfg, errors, _ = self._form(segments=[new_segment("1", "2"),
                                              new_segment("", "")]).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.frames, [1, 2])

    def test_bad_segment_reports_which_one(self):
        """报错必须说清是第几段 —— 界面上有好几行，只说"帧范围解析失败"等于没说。"""
        _, errors, _ = self._form(segments=[new_segment("1", "4"),
                                            new_segment("2 0", "9")]).to_config()
        self.assertTrue(any("第 2 段" in e for e in errors), errors)

    def test_zero_step_is_rejected(self):
        """步长 0 静默当 1 处理会让"每 5 帧"变成每帧都渲 —— 必须报错。"""
        _, errors, _ = self._form(segments=[new_segment("1", "100", "0")]).to_config()
        self.assertTrue(any("步长" in e for e in errors), errors)

    def test_half_resolution_rejected(self):
        cfg, errors, _ = self._form(width="1920").to_config()
        self.assertIsNone(cfg)
        self.assertTrue(any("宽高" in e for e in errors))

    def test_bad_int(self):
        cfg, errors, _ = self._form(samples="很多").to_config()
        self.assertIsNone(cfg)
        self.assertTrue(any("采样" in e for e in errors))

    def test_negative_restarts_means_unlimited(self):
        """-1 / unlimited 是合法输入，表示「一直重启直到渲完」，不该报错。"""
        cfg, errors, _ = self._form(max_restarts="-1").to_config()
        self.assertEqual(errors, [])
        self.assertTrue(cfg.unlimited_restarts)

    def test_unlimited_word(self):
        cfg, errors, _ = self._form(max_restarts="unlimited").to_config()
        self.assertEqual(errors, [])
        self.assertTrue(cfg.unlimited_restarts)

    def test_no_progress_limit(self):
        cfg, errors, _ = self._form(max_no_progress="5").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_no_progress_rounds, 5)

    def test_zero_frame_attempts_means_unlimited(self):
        cfg, errors, _ = self._form(max_frame_attempts="0").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_frame_attempts, 0)

    def test_numeric_options(self):
        cfg, errors, _ = self._form(samples="128", pct="50", width="1920",
                                    height="1080", engine="CYCLES",
                                    device="OPTIX", resume=False).to_config()
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
        _, errors, warns = self._form(blend=weird).to_config()
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


class TestSecondStartIsClean(unittest.TestCase):
    """「停止 → 再开始」之后，界面不能还挂着上一轮的结论。

    实测过的现象（取消一轮再点开始）：状态栏**整轮**都显示
    「已取消 · 完成 3/6 · 用时 1.9s」——「1.9s」还是上一轮的用时；进度条也会先
    跳回上一轮的百分比再归零（`on_start` 只把 `pb` 设 0，而 `_refresh` 每 80ms
    就用旧模型刷一次）。根因是 `job_start` 里手写了一份"重置清单"，漏掉了
    `cancelled / elapsed / current_frame / stalled / eta_sec / ok`，而
    `status_text()` 里 `cancelled` 判断在 `running` 之前，一路盖住了"正在渲染帧 N"。
    """

    @staticmethod
    def _cancelled_run(p):
        p.on_event("job_start", {"total": 6})
        p.on_event("frame_start", {"frame": 1})
        p.on_event("frame_done", {"frame": 1, "done": 1, "total": 6,
                                  "per_frame": 0.4, "eta_sec": 2.0, "eta_mode": "EMA"})
        p.on_event("frame_start", {"frame": 2})
        p.on_event("stall", {"phase": "Synchronizing object"})
        p.on_event("job_done", {"done": [1], "failed": {}, "exhausted": [], "total": 6,
                                "elapsed": 1.9, "restarts": 0, "cancelled": True,
                                "ok": False})

    def test_job_start_wipes_the_previous_verdict(self):
        p = ProgressModel()
        self._cancelled_run(p)
        self.assertTrue(p.cancelled)                 # 上一轮确实停在"已取消"

        p.on_event("job_start", {"total": 6, "done": 1})
        self.assertFalse(p.cancelled, "新一轮还在跑，状态栏不能写「已取消」")
        self.assertEqual(p.elapsed, 0.0, "上一轮的用时不能留在这一轮")
        self.assertIsNone(p.current_frame, "上一轮被砍断的那帧不能继续显示")
        self.assertIsNone(p.eta_sec)
        self.assertFalse(p.stalled)
        self.assertNotIn("已取消", p.status_text())

    def test_resume_position_shows_up_immediately(self):
        """续跑要在 job_start 那一刻就落在真实进度上 —— 否则看着像"断点没加载"。"""
        p = ProgressModel()
        p.on_event("job_start", {"total": 6, "done": 2})
        self.assertAlmostEqual(p.fraction, 2 / 6.0)
        self.assertIn("已完成 2/6", p.status_text())

    def test_job_start_without_done_starts_at_zero(self):
        """老版本 core 不带 `done` 时按 0 处理（别把 total 当成已完成）。"""
        p = ProgressModel()
        p.on_event("job_start", {"total": 6})
        self.assertEqual(p.done, 0)
        self.assertEqual(p.fraction, 0.0)

    def test_reset_clears_after_a_finished_run(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 4, "done": 0})
        p.on_event("frame_done", {"frame": 1, "done": 4, "total": 4})
        p.on_event("job_done", {"done": [1, 2, 3, 4], "failed": {}, "exhausted": [],
                                "total": 4, "elapsed": 3.0, "restarts": 0,
                                "cancelled": False, "ok": True})
        self.assertTrue(p.ok)
        p.reset()
        self.assertEqual((p.done, p.total, p.ok, p.finished, p.running), (0, 0, False, False, False))
        self.assertEqual(p.elapsed, 0.0)
        self.assertIsNone(p.finished_at)
        self.assertEqual(p.status_text(), "就绪")



class TestDoneButCancelled(unittest.TestCase):
    """最后一帧渲完的**同一瞬间**点了停止 → `ok` 与 `cancelled` 同时为真（P-W3）。

    `core` 里这两个值分别算、互不约束：`cancelled` 是"收到过取消请求"，
    `ok` 是"帧全在 done 里"。撞上时四处下游各自解释一遍，就会自相矛盾 ——
    存档按 ok 删了，界面却说「已取消 · 完成 6/6」，通知标题说「渲染已取消」，
    用户选的「渲完关机」也不执行（`aftermath_plan` 被 cancelled 一票否决）。
    统一口径：**ok 优先**，一个任务渲完了就是完成了。
    """

    @staticmethod
    def _done_and_cancelled(p, ok=True):
        p.on_event("job_start", {"total": 6})
        for i in range(1, 7):
            p.on_event("frame_done", {"frame": i, "done": i, "total": 6})
        p.on_event("job_done", {"done": [1, 2, 3, 4, 5, 6], "failed": {}, "exhausted": [],
                                "total": 6, "elapsed": 9.5, "restarts": 0,
                                "cancelled": True, "ok": ok})

    def test_status_says_complete_not_cancelled(self):
        p = ProgressModel()
        self._done_and_cancelled(p)
        text = p.status_text()
        self.assertNotIn("已取消", text)
        self.assertIn("全部完成 6/6", text)

    def test_status_still_says_cancelled_when_incomplete(self):
        """真取消（还有帧没渲）仍要说「已取消」—— 别把这条口径改坏。"""
        p = ProgressModel()
        p.on_event("job_start", {"total": 6})
        p.on_event("frame_done", {"frame": 1, "done": 1, "total": 6})
        p.on_event("job_done", {"done": [1], "failed": {}, "exhausted": [], "total": 6,
                                "elapsed": 1.9, "restarts": 0, "cancelled": True,
                                "ok": False})
        self.assertIn("已取消", p.status_text())

    def test_settle_clears_when_ok_wins(self):
        """存档那一侧本来就用 ok 判 —— 确认它和新的界面口径一致。"""
        self.assertEqual(settle_task_action(
            "job_done", {"ok": True, "cancelled": True}), ("clear", ""))

    def test_aftermath_runs_when_ok_wins(self):
        self.assertEqual(aftermath_plan(AFTERMATH_SHUTDOWN, True, True), "shutdown")


class TestCompletionClock(unittest.TestCase):
    """实际完成时刻：任务跑完，状态行必须写明「已于 x 完成」（与预估时间分开）。

    开工/结束的时刻是**界面上记**的（core 只报相对量 elapsed/eta_sec），
    所以这组用例钉的是"事件到达 → 时刻被记下 → 文案带上时刻"这条链。
    """

    def test_job_start_and_job_done_record_clock(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 2})
        self.assertIsNotNone(p.started_at)
        self.assertIsNone(p.finished_at)
        p.on_event("job_done", {"done": [1, 2], "total": 2, "ok": True,
                                "elapsed": 3.0, "failed": {}, "exhausted": [],
                                "restarts": 0})
        self.assertIsNotNone(p.finished_at)
        text = p.status_text()
        self.assertIn("已于", text)
        self.assertIn("完成", text)
        self.assertRegex(text, r"\d{2}:\d{2}:\d{2}")

    def test_failed_finish_also_shows_clock(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 4})
        p.on_event("job_done", {"done": [1, 2], "total": 4, "ok": False,
                                "elapsed": 5.0, "failed": {3: {}}, "exhausted": [4],
                                "restarts": 1})
        text = p.status_text()
        self.assertIn("未完成", text)
        self.assertIn("已于", text)

    def test_cancelled_says_stopped_not_completed(self):
        """取消不是"完成" —— 不能说「已于 x 完成」，要说"停止于"。"""
        p = ProgressModel()
        p.on_event("job_start", {"total": 4})
        p.on_event("job_done", {"done": [1], "total": 4, "ok": False, "cancelled": True,
                                "elapsed": 5.0, "failed": {}, "exhausted": [],
                                "restarts": 0})
        text = p.status_text()
        self.assertIn("已取消", text)
        self.assertIn("停止于", text)
        self.assertNotIn("已于", text)

    def test_eta_text_shows_estimated_finish_clock(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 10})
        p.eta_sec = 120.0
        p.eta_mode = "EMA"
        text = p.eta_text()
        self.assertIn("ETA", text)
        self.assertIn("预计", text)
        self.assertIn("完成", text)
        self.assertRegex(text, r"\d{2}:\d{2}")

    def test_eta_text_while_estimating_has_no_clock(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 10})
        self.assertEqual(p.eta_text(), "ETA 计算中…")

    def test_running_shows_elapsed(self):
        """运行中状态行要带「已用 X」（与 ETA 是一对：跑了多久 / 还要多久）。"""
        p = ProgressModel()
        p.on_event("job_start", {"total": 10})
        p.on_event("frame_start", {"frame": 3})
        text = p.status_text()
        self.assertIn("正在渲染帧 3", text)
        self.assertIn("已用", text)

    def test_elapsed_hidden_before_start(self):
        p = ProgressModel()
        self.assertNotIn("已用", p.status_text())

    def test_finished_hides_eta(self):
        p = ProgressModel()
        p.on_event("job_start", {"total": 2})
        p.eta_sec = 30.0
        p.on_event("job_done", {"done": [1, 2], "total": 2, "ok": True,
                                "elapsed": 3.0, "failed": {}, "exhausted": [],
                                "restarts": 0})
        self.assertEqual(p.eta_text(), "")


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

    def test_memory_cap_drops_oldest_and_counts(self):
        """界面来不及取时不能无限涨内存；**丢了多少必须记着** —— 要显示给人看。"""
        m = LogModel(maxlen=20)
        for i in range(30):
            m.add(str(i))
        self.assertEqual(len(m), 20)
        self.assertEqual(m.dropped, 10)
        self.assertEqual(m.total, 30)
        self.assertEqual(m.drain()[0], "10")     # 丢的是最早那批，不是最新的

    def test_clear_resets_dropped(self):
        m = LogModel(maxlen=2)
        for i in range(5):
            m.add(str(i))
        m.clear()
        self.assertEqual((len(m), m.dropped), (0, 0))


class TestNativeLineFilter(unittest.TestCase):
    """原生输出折叠器：既不刷屏，又**不能把 traceback 折掉**。

    原生输出默认开启（v0.8.1）之后，这条是"日志还能不能看"的开关：
    Blender 的采样进度是 `\\r` 原地刷新，一帧 64 采样能推几十行。
    """

    def setUp(self):
        self.now = [1000.0]
        self.f = NativeLineFilter(interval=1.0, clock=lambda: self.now[0])

    def _sample(self, frame=1, n=1):
        return "00:01.000  render | Fra: %d | Sample %d/64" % (frame, n)

    def test_first_sample_line_is_shown(self):
        self.assertEqual(self.f.feed(self._sample(1, 1)), [self._sample(1, 1)])

    def test_same_frame_samples_are_collapsed_within_interval(self):
        self.f.feed(self._sample(1, 1))
        self.assertEqual(self.f.feed(self._sample(1, 2)), [])
        self.assertEqual(self.f.feed(self._sample(1, 3)), [])

    def test_collapsed_count_is_reported_before_the_next_line(self):
        self.f.feed(self._sample(1, 1))
        self.f.feed(self._sample(1, 2))
        self.f.feed(self._sample(1, 3))
        self.now[0] += 2.0                      # 间隔够了，可以再放一条
        out = self.f.feed(self._sample(1, 4))
        self.assertEqual(len(out), 2)
        self.assertIn("已折叠 2 行", out[0])     # 先交代折了多少
        self.assertIn("Sample 4/64", out[1])

    def test_frame_change_is_shown_immediately(self):
        self.f.feed(self._sample(1, 1))
        out = self.f.feed(self._sample(2, 1))
        self.assertEqual(len(out), 1)
        self.assertIn("Fra: 2", out[0])

    def test_errors_are_never_collapsed(self):
        """错误行哪怕一模一样也要放行 —— 重复的 traceback 也是证据。"""
        for line in ("Error: Texture not found",
                     "Error: Texture not found",
                     "Traceback (most recent call last):",
                     "Warning: Cannot read file"):
            self.assertEqual(self.f.feed(line), [line], line)

    def test_plain_lines_are_not_collapsed(self):
        for line in ("正在构建 BVH", "Saved: 'C:/out/f_0001.png'",
                     "Loading render kernels (may take a few minutes)"):
            self.assertTrue(self.f.feed(line), line)

    def test_flush_reports_the_tail_once(self):
        self.f.feed(self._sample(1, 1))
        self.f.feed(self._sample(1, 2))
        tail = self.f.flush()
        self.assertEqual(len(tail), 1)
        self.assertIn("已折叠 1 行", tail[0])
        self.assertEqual(self.f.flush(), [])    # 只交代一次，不重复刷

    def test_reset_clears_pending(self):
        self.f.feed(self._sample(1, 1))
        self.f.feed(self._sample(1, 2))
        self.f.reset()
        self.assertEqual(self.f.flush(), [])
        self.assertEqual(len(self.f.feed(self._sample(1, 3))), 1)

    def test_blank_input_is_ignored(self):
        self.assertEqual(self.f.feed(""), [])
        self.assertEqual(self.f.feed(None), [])
        self.assertEqual(self.f.feed("   \r\n"), [])


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
    """帧号列表 → 紧凑文本（回显里"合并后是什么"那一句用）。"""

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


class TestFramesToSegments(unittest.TestCase):
    """帧号列表 → 帧段列表（开机续跑回填界面用）。

    存档里只有展开后的帧号，所以"每 5 帧"是**推断**出来的。这里钉两件事：
    ① 推断出的段（哪怕段数/步长与用户当初填的不同）能展开回**逐项相等**的帧号；
    ② 界面要显示的段描述确实是人话。
    """

    def test_single_run(self):
        self.assertEqual(frames_to_segments(list(range(1, 241))),
                         [new_segment("1", "240", "1")])

    def test_step_is_inferred(self):
        self.assertEqual(frames_to_segments([1, 6, 11, 16]),
                         [new_segment("1", "16", "5")])

    def test_step_change_starts_a_new_segment(self):
        self.assertEqual(frames_to_segments([1, 6, 11, 26]),
                         [new_segment("1", "11", "5"), new_segment("26", "", "")])

    def test_two_element_run(self):
        self.assertEqual(frames_to_segments([10, 20]),
                         [new_segment("10", "20", "10")])

    def test_empty(self):
        self.assertEqual(frames_to_segments([]), [])
        self.assertEqual(frames_to_segments(None), [])

    def test_roundtrip_is_exact(self):
        """★ 这条是"开机续跑跑的是同一批帧"的保证。"""
        for frames in ([1, 2, 3, 4], list(range(1, 9)), [1, 6, 11, 26],
                       [5], [10, 20], [1, 2, 3, 7, 8], [3, 1, 2], [1, 240, 300, 500, 505]):
            segs = frames_to_segments(frames)
            back, errors = segments_to_frames(segs)
            self.assertEqual(errors, [], frames)
            self.assertEqual(back, sorted(set(frames)), frames)

    def test_frame_text_roundtrip(self):
        """帧段 → 文本 → 帧号 也必须回到同一批（回显与 CLI 的 `-f` 是同一套写法）。"""
        for frames in ([1, 2, 3, 4, 9, 10], list(range(1, 241)) + [300]):
            segs = frames_to_segments(frames)
            text = frames_to_text(frames)
            self.assertTrue(text)                    # 只为确认这条链上文本非空
            back, errors = segments_to_frames(segs)
            self.assertEqual((errors, back), ([], sorted(set(frames))), text)


class TestSegmentLabel(unittest.TestCase):
    """一段 → 人话（界面上的实时回显直接显示它，"填了什么就得看到什么"）。"""

    def test_single_frame_never_looks_like_a_range(self):
        # 只填起帧 = 单帧。回显若写成 "250" 或 "250-"，用户会以为是要渲到工程末帧
        self.assertEqual(segment_label(new_segment("250")), "第 250 帧")
        self.assertEqual(segment_label(new_segment("250", "250")), "第 250 帧")

    def test_step_one_is_omitted(self):
        self.assertEqual(segment_label(new_segment("1", "250", "1")), "1-250")

    def test_step_is_shown(self):
        self.assertEqual(segment_label(new_segment("1", "100", "5")), "1-100 每 5 帧")

    def test_blank_segment(self):
        self.assertEqual(segment_label(new_segment("", "")), "（未填）")

    def test_reversed_bounds_are_normalised(self):
        self.assertEqual(segment_label(new_segment("9", "7")), "7-9")


class TestFramesPreview(unittest.TestCase):
    """界面那行实时回显：内容与合法/非法判定。"""

    def test_ok_text_lists_segments_and_total(self):
        frames, text, ok = frames_preview([new_segment("1", "4"),
                                           new_segment("9", "10")])
        self.assertTrue(ok)
        self.assertEqual(frames, [1, 2, 3, 4, 9, 10])
        self.assertIn("共 6 帧", text)
        self.assertIn("1-4", text)
        self.assertIn("9-10", text)

    def test_merged_form_shows_up_for_multiple_segments(self):
        """多段时补一句"合并后"，重叠/相接才看得出来到底渲哪些帧。"""
        _, text, ok = frames_preview([new_segment("1", "5"), new_segment("3", "8")])
        self.assertTrue(ok)
        self.assertIn("共 8 帧", text)
        self.assertIn("合并后", text)

    def test_error_points_at_the_segment(self):
        frames, text, ok = frames_preview([new_segment("1", "4"),
                                           new_segment("x", "9")])
        self.assertFalse(ok)
        self.assertEqual(frames, [1, 2, 3, 4])       # 好的那几段仍返回，便于排错
        self.assertIn("第 2 段", text)

    def test_empty_is_not_ok(self):
        frames, text, ok = frames_preview([new_segment("", "")])
        self.assertFalse(ok)
        self.assertEqual(frames, [])
        self.assertIn("没有可渲染的帧", text)

    def test_preview_never_lists_a_huge_frame_list(self):
        """回显不能长到撑破那一行。"""
        _, text, ok = frames_preview([new_segment("1", "100000")])
        self.assertTrue(ok)
        self.assertLess(len(text), 80)


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

    def test_old_five_maps_to_the_plain_label(self):
        """老存档里的 5 次要显示成「5 次」，**不能**显示成默认项 ——
        界面默认值改成"一直重启"之后，把 5 也映射到默认项就等于改写了存档语义。"""
        self.assertEqual(restart_label_for(5), "5 次")
        self.assertNotEqual(restart_label_for(5), DEFAULT_RESTART_OPTION)


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
        # 这里故意用 `max_restarts=-1`（一直重启）+ 有限的限次：那是 CLI 与内核都支持的
        # 组合（`test_core.py` 的 no-progress 护栏用例就靠它），往返必须**逐字段恒等**。
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
        cfg, errors, warns = FormModel.from_config(d, "C:/bl/blender.exe").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(warns, [])
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.to_dict(), d)

    def test_frames_are_restored_as_segments_that_expand_back(self):
        """区间展开要乘步长：回填走 `frames_to_segments`，展开必须逐项回到同一批帧
        （早先是把 step 置回 1 再填文本；现在段自带步长，判据更强）。"""
        fm = FormModel.from_config(self._dict(frames=list(range(1, 9))))
        self.assertEqual(fm.segments, [new_segment("1", "8", "1")])
        back, errors = segments_to_frames(fm.segments)
        self.assertEqual(errors, [])
        self.assertEqual(back, list(range(1, 9)))

    def test_restored_segments_keep_step(self):
        """★ 存档里"每 5 帧"的帧号回填后，界面上得看得出是每 5 帧、且展开不变。"""
        fm = FormModel.from_config(self._dict(frames=[1, 6, 11, 16]))
        self.assertEqual(fm.segments, [new_segment("1", "16", "5")])
        back, _ = segments_to_frames(fm.segments)
        self.assertEqual(back, [1, 6, 11, 16])

    def test_carries_fields_that_have_no_widget(self):
        """extra_args / restart_delay 界面上没有控件，但**必须原样带过去**：
        丢了它们，重建的任务会静默按默认值跑。"""
        d = self._dict(extra_args=["--cycles-device", "OPTIX"], restart_delay=0.25)
        fm = FormModel.from_config(d, "b.exe")
        self.assertEqual(fm.extra_args, ["--cycles-device", "OPTIX"])
        self.assertEqual(fm.restart_delay, 0.25)
        cfg, _, _ = fm.to_config()
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
        # 存档里缺 `max_restarts` = 老存档/坏存档 → 按**有界**的 5 次兜（不是新任务的默认值）。
        # 与 restore_limits_backup 同一条原则：分不出来的时候按有界那一侧。
        self.assertEqual(fm.max_restarts, "5 次")


class TestUnlimitedRestartDefaults(unittest.TestCase):
    """选「一直重启」⇒ 两个限次框**默认填 0（不限）**，但**不锁**、可改回去。

    为什么仅仅是"默认"而不是"强制"：`max_no_progress_rounds` 是内核里**唯一**
    防死循环的闸门（卡在同一帧反复崩时，光靠重启次数永远不会停）。填 0 是体贴的默认值，
    但把用户改回去的路堵死就等于把这个闸门删了。所以这里的用例一半在盯"默认填 0"，
    一半在盯"改回去必须真的生效"（防止有人把默认值实现成强制值）。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix=".brc-unlim-")
        self.blend = os.path.join(self.d, "house.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER-v5")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _form(self, restart, attempts, no_progress):
        return FormModel(blend=self.blend, output=os.path.join(self.d, "out", "f_####"),
                         segments=[new_segment("1", "3")], max_restarts=restart,
                         max_frame_attempts=attempts, max_no_progress=no_progress)

    def test_only_the_unlimited_label_counts_as_unlimited(self):
        self.assertTrue(is_unlimited_restart(UNLIMITED_RESTART_LABEL))
        self.assertTrue(is_unlimited_restart("一直重启"))
        self.assertTrue(is_unlimited_restart("-1"))
        for label in RESTART_OPTIONS[:-1]:
            self.assertFalse(is_unlimited_restart(label), label)
        # 认不出来的值**不能**当成"一直重启"（否则会悄悄把限次全放开）
        self.assertFalse(is_unlimited_restart(""))
        self.assertFalse(is_unlimited_restart("瞎写"))

    def test_unlimited_option_opens_attempts_but_keeps_the_guard(self):
        """界面拿这个返回值去填两个框：单帧尝试放开，无进展兜底**不能**一起放开。

        旧版返回 ("0", "0")，等于选完"一直重启"就再没有任何终止条件
        （卡在同一处反复崩时会一直转到有人来停）—— 见 AGENTS 坑 39。
        """
        self.assertEqual(unlimited_limit_fill(UNLIMITED_RESTART_LABEL),
                         ("0", DEFAULT_NO_PROGRESS))
        self.assertEqual(UNLIMITED_LIMITS, ("0", DEFAULT_NO_PROGRESS))

    def test_finite_option_fills_nothing(self):
        """有限次数时不能动用户填的值 —— None = 别碰。"""
        for label in RESTART_OPTIONS[:-1]:
            self.assertIsNone(unlimited_limit_fill(label), label)
        self.assertIsNone(unlimited_limit_fill("瞎写"))

    def test_fill_turns_into_unlimited_attempts_with_guard_intact(self):
        cfg, errors, _ = self._form(UNLIMITED_RESTART_LABEL, *UNLIMITED_LIMITS).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_frame_attempts, 0)
        # ★ 这条是本次修订的核心：默认"一直重启"之后，防死循环闸门必须还在
        self.assertEqual(cfg.max_no_progress_rounds, int(DEFAULT_NO_PROGRESS))
        self.assertTrue(cfg.unlimited_restarts)

    def test_user_can_put_the_guard_back(self):
        """护栏用例：把默认值实现成强制值的话，这条会红。"""
        cfg, errors, _ = self._form(UNLIMITED_RESTART_LABEL, "9", "3").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_frame_attempts, 9)
        self.assertEqual(cfg.max_no_progress_rounds, 3)
        self.assertTrue(cfg.unlimited_restarts)

    def test_finite_restart_keeps_the_typed_limits(self):
        cfg, errors, _ = self._form("5 次", "9", "7").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_frame_attempts, 9)
        self.assertEqual(cfg.max_no_progress_rounds, 7)
        self.assertFalse(cfg.unlimited_restarts)

    def test_negative_no_progress_still_rejected(self):
        _, errors, _ = self._form("5 次", "3", "-2").to_config()
        self.assertTrue(any("无进展" in e for e in errors))

    def test_unlimited_roundtrip_is_stable(self):
        """存档（一直重启 + 0/0）→ 表单 → 配置，必须还是同一份 —— 开机续跑靠这条。"""
        from brconsole.core import JobConfig
        d = JobConfig(blend=self.blend, frames=[1, 2, 3],
                      output_template=os.path.join(self.d, "out", "f_####"),
                      max_restarts=-1, max_frame_attempts=0,
                      max_no_progress_rounds=0).to_dict()
        cfg, errors, _ = FormModel.from_config(d, "b.exe").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.to_dict(), d)

    def test_restored_unlimited_form_reads_back_as_is(self):
        """回填后界面上是两个 0（不残留 3）—— 否则"一直重启"底下写着"3"很迷惑。"""
        fm = FormModel.from_config(
            {"max_restarts": -1, "max_frame_attempts": 0, "max_no_progress_rounds": 0},
            "b.exe")
        self.assertEqual((fm.max_frame_attempts, fm.max_no_progress), ("0", "0"))
        self.assertTrue(is_unlimited_restart(fm.max_restarts))

    # ---- 离开「一直重启」时必须回到**有界**（实测抓到的漏网之鱼）----

    def test_switching_between_finite_options_never_touches_typed_values(self):
        """反向护栏：有限 → 有限 时**一个字都不能改**。

        这是"离开一直重启就填 3/3"那种粗暴修法会砸掉的东西：用户手填 9/7 之后
        只是想换个重启次数，值却被重置回默认。所以修复必须走 backup 这条路，
        不能无条件复位。
        """
        for start in RESTART_OPTIONS[:-1]:
            for target in RESTART_OPTIONS[:-1]:
                self.assertEqual(
                    restart_limits_plan(target, "9", "7", None), ("9", "7", None),
                    "%s → %s" % (start, target))

    def test_selecting_unlimited_twice_keeps_the_original_backup(self):
        """在「一直重启」态里再点一次，不能把 0/0 存成备份（否则回头"还原"出一对不限值）。"""
        a, n, backup = restart_limits_plan(UNLIMITED_RESTART_LABEL, "9", "7", None)
        self.assertEqual((a, n), ("0", DEFAULT_NO_PROGRESS))
        self.assertEqual(backup, ("9", "7"))
        a, n, backup = restart_limits_plan(UNLIMITED_RESTART_LABEL, a, n, backup)
        self.assertEqual(backup, ("9", "7"))        # 没被 0/0 覆盖
        self.assertEqual(restart_limits_plan("5 次", a, n, backup)[:2],
                         ("9", "7"))

    def test_leaving_unlimited_after_archive_restore_returns_to_bounded_defaults(self):
        """★ 存档回填 → 切回有限次数：必须是 3/3，**不能停在 0/0**。

        实测抓到的：回填路径不留 backup，切回时无处可还原，两个框就停在 0（=不限），
        于是用户选的"5 次（明确有限）"底下配着"单帧不限次数 + 无进展不兜底" ——
        卡在同一帧会一直重试，`no_progress` 这个唯一的死循环闸门被静默关掉。
        这条用例同时钉住"有界"这个结果本身（不只是钉两个控件变量的值）。
        """
        backup = restore_limits_backup(UNLIMITED_RESTART_LABEL, "0", "0")
        self.assertEqual(backup, (DEFAULT_ATTEMPTS, DEFAULT_NO_PROGRESS))
        a, n, left = restart_limits_plan("5 次", "0", "0", backup)
        self.assertEqual((a, n), (DEFAULT_ATTEMPTS, DEFAULT_NO_PROGRESS))
        self.assertIsNone(left)                     # 用完即弃

        cfg, errors, _ = self._form("5 次", a, n).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_restarts, 5)
        self.assertEqual(cfg.max_frame_attempts, int(DEFAULT_ATTEMPTS))
        self.assertEqual(cfg.max_no_progress_rounds, int(DEFAULT_NO_PROGRESS))
        self.assertFalse(cfg.unlimited_restarts)

    def test_restore_backup_keeps_deliberate_values_and_skips_finite(self):
        """回填的备份：非 0（用户特意填的）保留，0 换成默认；有限次数时不预置。"""
        self.assertEqual(restore_limits_backup(UNLIMITED_RESTART_LABEL, "9", "0"),
                         ("9", DEFAULT_NO_PROGRESS))
        self.assertEqual(restore_limits_backup(UNLIMITED_RESTART_LABEL, "0", "7"),
                         (DEFAULT_ATTEMPTS, "7"))
        for label in RESTART_OPTIONS[:-1]:
            self.assertIsNone(restore_limits_backup(label, "0", "0"), label)
        self.assertIsNone(restore_limits_backup("瞎写", "0", "0"))

    def test_full_roundtrip_restore_then_leave_unlimited(self):
        """把两条路串起来（回填 → 切选项 → 建档），断言最终配置是有界的。"""
        d = {"max_restarts": -1, "max_frame_attempts": 0, "max_no_progress_rounds": 0}
        fm = FormModel.from_config(d, "b.exe")
        # from_config 是"按存档重填参数"，不含工程/输出（那两项在存档里另有字段）
        fm.blend, fm.output = self.blend, os.path.join(self.d, "out", "f_####")
        backup = restore_limits_backup(fm.max_restarts, fm.max_frame_attempts,
                                       fm.max_no_progress)
        a, n, _ = restart_limits_plan("10 次", fm.max_frame_attempts, fm.max_no_progress,
                                      backup)
        fm.max_frame_attempts, fm.max_no_progress = a, n
        fm.max_restarts = "10 次"
        cfg, errors, _ = fm.to_config()
        self.assertEqual(errors, [])
        self.assertEqual((cfg.max_restarts, cfg.max_frame_attempts,
                          cfg.max_no_progress_rounds),
                         (10, int(DEFAULT_ATTEMPTS), int(DEFAULT_NO_PROGRESS)))


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


class TestAftermathPlan(unittest.TestCase):
    """任务结束后的关机/睡眠决策 —— 只认「全部渲完」（`ok`），其他一律不动。

    ⚠️ `cancelled` 曾经也有一票否决权，走查（P-W3）把它拿掉了：
    最后一帧渲完的同一瞬间点停止，`ok` 与 `cancelled` 会同时为真
    （`core` 里两个值分别算、互不约束）。这时按 cancelled 拦掉，用户选好的
    「渲完关机」就不会执行 —— 早上起来图全渲完了、机器却开着。
    真取消（还有帧没渲）时 `ok` 本来就是 False，自然不触发，不需要再判 cancelled。
    """

    def test_shutdown_only_on_full_success(self):
        self.assertEqual(aftermath_plan(AFTERMATH_SHUTDOWN, True, False), "shutdown")

    def test_sleep_only_on_full_success(self):
        self.assertEqual(aftermath_plan(AFTERMATH_SLEEP, True, False), "sleep")

    def test_cancelled_but_complete_still_triggers(self):
        """最后一帧渲完与取消撞在一起：`ok` 说话，关掉 cancelled 的一票否决。"""
        self.assertEqual(aftermath_plan(AFTERMATH_SHUTDOWN, True, True), "shutdown")
        self.assertEqual(aftermath_plan(AFTERMATH_SLEEP, True, True), "sleep")

    def test_failure_and_give_up_never_trigger(self):
        self.assertIsNone(aftermath_plan(AFTERMATH_SHUTDOWN, False, False))
        self.assertIsNone(aftermath_plan(AFTERMATH_SLEEP, False, True))

    def test_nothing_and_garbage_are_noop(self):
        self.assertIsNone(aftermath_plan(AFTERMATH_NOTHING, True, False))
        self.assertIsNone(aftermath_plan("", True, False))
        self.assertIsNone(aftermath_plan("关机", True, False))     # 界面存的是值不是文案
        self.assertIsNone(aftermath_plan(None, True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
