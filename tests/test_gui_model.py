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
from brconsole.core import UNLIMITED  # noqa: E402
from brconsole.guimodel import (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN,  # noqa: E402
                                AFTERMATH_SLEEP, DEFAULT_NO_PROGRESS, DEFAULT_RETRY,
                                RETRY_CHOICES, RETRY_COUNTS, RETRY_UNLIMITED_LABEL,
                                SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL, FormModel,
                                LogModel, NativeLineFilter, ProgressModel,
                                aftermath_plan, event_line,
                                fields_from_detail, frames_preview, frames_to_text,
                                frames_to_segments, guess_state_path, new_segment,
                                retry_count, retry_label_for, retry_label_from_config,
                                retry_note, retry_plan, segment_label,
                                segments_to_frames, scene_to_config,
                                settle_task_action, state_summary)
from brconsole.state import JobState  # noqa: E402


class TestRetryChoices(unittest.TestCase):
    """失败重试：**一个下拉，一个次数**，次数同时管重启和单帧尝试。

    界面上原来是「崩溃后重启」下拉（1/3/5/10/一直）+「连续无进展」+「单帧最多尝试」
    两个数字框 + 两行长说明。用户的口径是"如果选一直重复，就是所有的都一直重复；
    如果选 3 次，那其他的尝试也是三次" —— 这三件东西本来就该是同一个数。
    现在下拉的文案就是那个数：

        一直重复，直到渲完  →  (UNLIMITED, 0, 3)
        3 次               →  (3, 3, 3)
    """

    def test_unlimited_means_everything_unlimited(self):
        self.assertEqual(retry_plan(RETRY_UNLIMITED_LABEL),
                         (UNLIMITED, 0, DEFAULT_NO_PROGRESS))

    def test_a_number_means_the_same_number_for_every_knob(self):
        """★ 用户的原话："如果选 3 次，那其他的尝试也是三次"。"""
        for n in RETRY_COUNTS:
            self.assertEqual(retry_plan("%d 次" % n), (n, n, DEFAULT_NO_PROGRESS),
                             "%d 次" % n)

    def test_choices_are_unlimited_plus_the_numbers(self):
        self.assertEqual(RETRY_CHOICES[0], RETRY_UNLIMITED_LABEL)
        self.assertEqual(len(RETRY_CHOICES), len(RETRY_COUNTS) + 1)
        self.assertEqual(list(RETRY_CHOICES), list(dict.fromkeys(RETRY_CHOICES)))

    def test_default_is_unlimited(self):
        """默认「一直重复」：崩溃续跑就是这个程序的卖点，有限次数会让长任务
        在"其实还能接着跑"的时候提前放弃。"""
        self.assertEqual(DEFAULT_RETRY, RETRY_UNLIMITED_LABEL)
        self.assertIn(DEFAULT_RETRY, RETRY_CHOICES)
        self.assertEqual(FormModel().retry, DEFAULT_RETRY)

    def test_no_progress_guard_never_follows_the_dropdown(self):
        """⚠️ 兜底恒为 3 轮，**不跟这个下拉走** —— 它是内核里唯一的防死循环闸门。

        它和"重试次数"不是一回事：管的是"再试也没意义"的判据（连续几轮一帧都没推进）。
        跟着变成不限就等于把这个闸门关掉（AGENTS 坑 39）。
        """
        for label in RETRY_CHOICES:
            _r, _a, no_progress = retry_plan(label)
            self.assertEqual(no_progress, DEFAULT_NO_PROGRESS, label)
            self.assertGreater(no_progress, 0, label)

    def test_every_choice_round_trips(self):
        """文案 ↔ 次数一一对应：文案改了但解析没跟上，回填时下拉就会漂。"""
        for label in RETRY_CHOICES:
            self.assertEqual(retry_label_for(retry_count(label)), label)
        self.assertEqual(retry_count("7 次"), 7)          # 预设外的也要认

    def test_unknown_label_falls_back_to_bounded(self):
        """认不出来的文案按**有界**兜 —— 宁可少试几次，也别悄悄变成无限。"""
        self.assertEqual(retry_plan("随便写的"), (5, 5, DEFAULT_NO_PROGRESS))
        self.assertEqual(retry_plan(""), (5, 5, DEFAULT_NO_PROGRESS))

    def test_note_only_says_what_changed(self):
        """说明只回答"这一次放宽/收紧了什么"，不做参数教学（原来两行各 40+ 字）。"""
        for label in RETRY_CHOICES:
            note = retry_note(label)
            self.assertLessEqual(len(note), 40, note)
            self.assertNotIn("填 0", note)
        self.assertIn("都不限", retry_note(RETRY_UNLIMITED_LABEL))
        self.assertIn("3 次", retry_note("3 次"))
        self.assertIn(str(DEFAULT_NO_PROGRESS), retry_note("3 次"))

    # ---- 存档往返：老存档里的数要如实回到下拉 ----
    def test_archive_unlimited_maps_to_unlimited_label(self):
        self.assertEqual(retry_label_from_config(
            {"max_restarts": UNLIMITED, "max_frame_attempts": 0}),
            RETRY_UNLIMITED_LABEL)

    def test_archive_numbers_map_to_their_own_label(self):
        for n in (1, 3, 5, 10):
            self.assertEqual(retry_label_from_config({"max_restarts": n}), "%d 次" % n)

    def test_archive_with_a_number_outside_the_presets_is_kept(self):
        """老存档里可能是 7 次（CLI 能填）—— 照样显示「7 次」，不折叠成 5 或 10。
        折叠就等于"回填一下，行为变了"。"""
        self.assertEqual(retry_label_from_config({"max_restarts": 7}), "7 次")
        self.assertEqual(retry_plan("7 次"), (7, 7, DEFAULT_NO_PROGRESS))

    def test_archive_missing_fields_falls_back_to_bounded(self):
        """读不出来时按**有界**那一侧兜 —— 安全方向。"""
        self.assertEqual(retry_label_from_config({}), "5 次")
        self.assertEqual(retry_label_from_config(None), "5 次")

    def test_archive_roundtrip_preserves_behavior(self):
        """存档 → 表单 → JobConfig：重启次数不变，单帧尝试跟着同一个数走。"""
        d = tempfile.mkdtemp(prefix=".brc-retry-")
        try:
            blend = os.path.join(d, "a.blend")
            with open(blend, "wb") as f:
                f.write(b"BLENDER-v5")
            for cfg_dict, want_restarts, want_attempts in (
                    ({"max_restarts": UNLIMITED, "max_frame_attempts": 0}, UNLIMITED, 0),
                    ({"max_restarts": 10, "max_frame_attempts": 3}, 10, 10),
                    ({"max_restarts": 7, "max_frame_attempts": 3}, 7, 7)):
                fm = FormModel.from_config(dict(
                    cfg_dict, blend=blend,
                    output_template=os.path.join(d, "o", "f_####"), frames=[1]))
                cfg, errors, _w = fm.to_config()
                self.assertEqual(errors, [], cfg_dict)
                self.assertEqual(cfg.unlimited_restarts, want_restarts < 0, cfg_dict)
                if want_restarts >= 0:
                    self.assertEqual(cfg.max_restarts, want_restarts, cfg_dict)
                self.assertEqual(cfg.max_frame_attempts, want_attempts, cfg_dict)
        finally:
            shutil.rmtree(d, ignore_errors=True)


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

    # ---- 下拉的两档落到 JobConfig ----
    #
    # 走查 P-W4 当时是「三个『不限』同时成立就警告」：界面把重启次数 / 单帧尝试 /
    # 无进展兜底拆成三个可填控件，用户照着理解填 0 就能凑出一个**没有任何终止条件**的
    # 组合。现在两个数字框没了，那个组合从界面上拼不出来（`retry_plan` 里兜底恒为 3），
    # 警告也一起删了 —— **修复杂度比警告复杂度更好**。
    def test_unlimited_choice_passes_unlimited_through(self):
        cfg, errors, warns = self._form(retry=RETRY_UNLIMITED_LABEL).to_config()
        self.assertEqual(errors, [])
        self.assertEqual(warns, [])
        self.assertTrue(cfg.unlimited_restarts)
        self.assertEqual(cfg.max_frame_attempts, 0)
        self.assertEqual(cfg.max_no_progress_rounds, DEFAULT_NO_PROGRESS)

    def test_number_choice_bounds_both_knobs_to_that_number(self):
        for n in RETRY_COUNTS:
            cfg, errors, warns = self._form(retry="%d 次" % n).to_config()
            self.assertEqual(errors, [], n)
            self.assertEqual(warns, [], n)
            self.assertFalse(cfg.unlimited_restarts, n)
            self.assertEqual(cfg.max_restarts, n, n)
            self.assertEqual(cfg.max_frame_attempts, n, n)   # ★ 不是各填各的

    def test_no_termination_free_config_is_reachable_from_the_form(self):
        """★ 界面拼不出"三重不限" —— 这是把两个数字框并进下拉换来的硬保证。"""
        for label in RETRY_CHOICES:
            cfg, _e, _w = self._form(retry=label).to_config()
            self.assertGreater(cfg.max_no_progress_rounds, 0, label)

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

    def test_retry_knobs_are_no_longer_form_fields(self):
        """三个重试参数已经不是表单字段了：表单只认一个下拉文案 `retry`。

        这条同时是一道防腐断言 —— 如果哪天有人把 `max_restarts` 之类的字段加回
        `FormModel`，它会失败，提醒"先想清楚要不要把界面复杂度加回来"。
        `FormModel(**{"max_restarts": ...})` 里那些多余 kw 会被**静默忽略**
        （构造器用 `kw.get`），所以这里直接查属性在不在，而不是查行为。
        """
        fm = self._form(max_restarts="-1", max_frame_attempts="0", max_no_progress="5")
        for gone in ("max_restarts", "max_frame_attempts", "max_no_progress"):
            self.assertFalse(hasattr(fm, gone), "%s 又回到 FormModel 了" % gone)
        cfg, errors, _ = fm.to_config()
        self.assertEqual(errors, [])
        # 多余 kw 被忽略后仍按默认档（尽力渲完）跑，不会因为"填了 5"就变 5
        self.assertTrue(cfg.unlimited_restarts)
        self.assertEqual(cfg.max_no_progress_rounds, int(DEFAULT_NO_PROGRESS))

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
        # 用「尽力渲完」那一档（max_restarts=-1 + 单帧不限 + 兜底 3 轮）：界面能**无损**
        # 表达的就是这一档和「有界」那一档，往返必须逐字段恒等。
        args = dict(blend=self.blend, frames=[1, 2, 3, 7, 8],
                    output_template=os.path.join(self.d, "out", "f_####"),
                    engine="CYCLES", samples=64, device="CPU", resolution=(1920, 1080),
                    resolution_percentage=50, file_format="PNG", scene="SceneB",
                    max_restarts=-1, max_frame_attempts=0,
                    max_no_progress_rounds=int(DEFAULT_NO_PROGRESS),
                    resume=False, restart_delay=1.5, extra_args=["-x"])
        args.update(kw)
        return JobConfig(**args).to_dict()

    def test_unrepresentable_guard_value_is_normalised_not_dropped(self):
        """存档里把兜底写成 7 轮（CLI 能填，界面那个下拉表达不了）→ 回填后按 3 轮跑。

        ⚠️ 关键是**不能变成 0**：那等于把内核里唯一的防死循环闸门关掉（AGENTS 坑 39）。
        收窄可以（界面表达不了更细的值），但收窄的方向必须是"更安全"。
        """
        d = self._dict(max_no_progress_rounds=7)
        cfg, errors, _ = FormModel.from_config(d, "b.exe").to_config()
        self.assertEqual(errors, [])
        self.assertEqual(cfg.max_no_progress_rounds, int(DEFAULT_NO_PROGRESS))
        self.assertTrue(cfg.unlimited_restarts)     # 「尽力渲完」这一档没被改掉

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
        # 存档里缺 `max_restarts` = 老存档/坏存档 → 按**有界**那侧兜（不是新任务的默认值）。
        # 与 `retry_label_from_config` 同一条原则：分不出来的时候按有界那一侧。
        self.assertEqual(fm.retry, "5 次")




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
