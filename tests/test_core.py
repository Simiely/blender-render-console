# -*- coding: utf-8 -*-
"""调度核心的端到端测试。

不启动真 Blender（另见 tools/smoke_real_blender.py 做真机实测），
而是让 `fake_blender.py` 输出**与真机同构**的行（原生行用 \\r 刷新 + ##PROG## JSON），
这样子进程管理、双通道解析、崩溃续跑、取消这些**接线**都能在 CI 里测到 ——
只测 service 层是测不出接线断没断的。
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from brconsole.core import (UNLIMITED, JobConfig, RenderJob,  # noqa: E402
                            default_cmd_factory, parse_restart_limit)
from brconsole.state import JobState  # noqa: E402

FAKE = os.path.join(HERE, "fake_blender.py")


def fake_factory(blender_exe, blend, driver_path, job_json, extra_args=()):
    """把 core 拼出来的 Blender 命令换成「假 Blender」命令。"""
    return [blender_exe, FAKE, "--job", job_json] + list(extra_args)


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=".brc-core-")
        self.blend = os.path.join(self.dir, "scene.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER-v5")            # core 只检查存在性
        self.out = os.path.join(self.dir, "out", "frame_####")
        self.state = os.path.join(self.dir, "out", ".render_state.json")

    def tearDown(self):
        try:
            shutil.rmtree(self.dir)
        except Exception:
            pass

    def make_job(self, frames=(1, 2, 3, 4), extra=(), **kw):
        cfg = JobConfig(
            blend=self.blend, frames=list(frames), output_template=self.out,
            state_path=self.state, engine="CYCLES", samples=24,
            # 默认 sleep 放前面，extra 里的 --sleep 才能覆盖它（argparse 取最后一个）
            extra_args=["--sleep", "0.01"] + list(extra),
            restart_delay=0.05, **kw)
        events = []
        job = RenderJob(cfg, sys.executable, cmd_factory=fake_factory)
        return job, cfg, events

    def produced(self):
        d = os.path.join(self.dir, "out")
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d) if f.startswith("frame_"))

    def run_job(self, job, events):
        return job.run(on_event=lambda kind, ev: events.append((kind, ev)))


class TestHappyPath(_Base):
    def test_all_frames_done(self):
        job, cfg, events = self.make_job()
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["done"], [1, 2, 3, 4])
        self.assertEqual(r["restarts"], 0)
        self.assertEqual(len(self.produced()), 4)

    def test_state_file_written(self):
        job, cfg, events = self.make_job()
        self.run_job(job, events)
        st = JobState.load(self.state)
        self.assertIsNotNone(st)
        self.assertEqual(sorted(st.done), [1, 2, 3, 4])
        self.assertTrue(all(v["secs"] is not None for v in st.done.values()))

    def test_event_sequence(self):
        job, cfg, events = self.make_job()
        self.run_job(job, events)
        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "job_start")
        self.assertEqual(kinds[-1], "job_done")
        self.assertEqual(kinds.count("frame_done"), 4)
        self.assertEqual(kinds.count("frame_start"), 4)
        self.assertIn("native", kinds)          # 原生行兜底通道真的收到了东西
        self.assertNotIn("stall", kinds)        # 快任务不该误报静默
        self.assertNotIn("crash", kinds)

    def test_native_channel_parses_phase_and_saved(self):
        """原生行通道要能给出「阶段文案」和「已落盘」——这是没有百分比时的唯一信号。"""
        job, cfg, events = self.make_job()
        self.run_job(job, events)
        natives = [ev for k, ev in events if k == "native"]
        phases = {ev["phase"] for ev in natives if "phase" in ev}
        self.assertIn("正在编译着色器 / 加载渲染 kernel（首次可能数分钟）", phases)
        self.assertTrue(any("saved" in ev for ev in natives))

    def test_eta_first_frame_is_warmup(self):
        job, cfg, events = self.make_job()
        self.run_job(job, events)
        dones = [ev for k, ev in events if k == "frame_done"]
        self.assertTrue(dones[0]["warmup"])              # 首帧预热
        self.assertFalse(dones[1]["warmup"])
        self.assertIsNotNone(dones[-1]["eta_sec"])


class TestCrashResume(_Base):
    def test_resume_after_single_crash(self):
        """第一轮崩在第 3 帧，第二轮只渲染 3、4 —— 1、2 不重渲染。"""
        job, cfg, events = self.make_job(extra=["--fail-at", "3", "--fail-first-run-only"])
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["done"], [1, 2, 3, 4])
        self.assertEqual(r["restarts"], 1)
        self.assertEqual(len(self.produced()), 4)

        kinds = [k for k, _ in events]
        self.assertIn("crash", kinds)
        self.assertIn("restart", kinds)
        # 续跑那轮只该有 3、4 两个 frame_start
        starts_after_restart = [
            ev["frame"] for k, ev in events
            if k == "frame_start" and kinds.index("restart") < len(kinds)
        ]
        self.assertEqual(starts_after_restart[-2:], [3, 4])

    def test_persistent_crash_exhausts_that_frame(self):
        """某帧一直崩：耗尽尝试额度后被标记放弃，其余帧继续渲染（不能整轮卡死）。"""
        job, cfg, events = self.make_job(extra=["--fail-at", "3"],
                                         max_frame_attempts=3, max_restarts=8)
        r = self.run_job(job, events)
        self.assertFalse(r["ok"])
        self.assertEqual(r["done"], [1, 2, 4])      # 3 放弃，4 最终渲染出来了
        self.assertEqual(r["exhausted"], [3])
        self.assertGreaterEqual(r["restarts"], 2)

    def test_no_progress_is_not_silent(self):
        """一直崩在同一帧时，界面必须看到 give_up 或 exhausted，不能默默转圈。"""
        job, cfg, events = self.make_job(extra=["--fail-at", "3"],
                                         max_frame_attempts=2, max_restarts=1)
        r = self.run_job(job, events)
        self.assertFalse(r["ok"])
        self.assertTrue(any(k in ("give_up", "crash") for k, _ in events))


class TestStateReuse(_Base):
    def test_second_invocation_resumes(self):
        """第一次崩掉，第二次用**同一条命令**跑：只渲染剩余帧。"""
        job1, cfg1, ev1 = self.make_job(extra=["--fail-at", "3", "--fail-first-run-only"])
        r1 = self.run_job(job1, ev1)
        self.assertTrue(r1["ok"])

        # 换个场景：第一轮崩在 2，然后手动中止（max_restarts=0）
        shutil.rmtree(os.path.join(self.dir, "out"))
        job2, cfg2, ev2 = self.make_job(frames=(1, 2, 3), extra=["--fail-at", "2"],
                                        max_restarts=0)
        r2 = self.run_job(job2, ev2)
        self.assertEqual(r2["done"], [1])
        self.assertFalse(r2["ok"])

        # 第二次：不再崩 —— 应该只渲染 2、3
        job3, cfg3, ev3 = self.make_job(frames=(1, 2, 3), extra=[])
        r3 = self.run_job(job3, ev3)
        self.assertTrue(r3["ok"], r3)
        self.assertEqual(r3["done"], [1, 2, 3])
        starts = [ev["frame"] for k, ev in ev3 if k == "frame_start"]
        self.assertEqual(starts, [2, 3])            # 关键：1 没有重渲染
        self.assertTrue(any(k == "resume" for k, _ in ev3))

    def test_mismatched_state_starts_fresh(self):
        """工程/输出/帧范围变了就别续 —— 否则会把别的任务的进度算进来。"""
        job, cfg, ev = self.make_job(frames=(1, 2))
        self.run_job(job, ev)

        other_out = os.path.join(self.dir, "out2", "frame_####")
        cfg2 = JobConfig(blend=self.blend, frames=[5, 6], output_template=other_out,
                         state_path=self.state)
        job2 = RenderJob(cfg2, sys.executable, cmd_factory=fake_factory)
        r = job2.run(on_event=lambda k, e: ev.append((k, e)))
        warns = [e["msg"] for k, e in ev if k == "warn"]
        self.assertTrue(any("不匹配" in w for w in warns), warns)
        self.assertEqual(r["done"], [5, 6])

    def test_no_resume_flag(self):
        job, cfg, ev = self.make_job(frames=(1, 2, 3), extra=["--fail-at", "2"],
                                     max_restarts=0)
        self.run_job(job, ev)
        job2, cfg2, ev2 = self.make_job(frames=(1, 2, 3), resume=False)
        r = job2.run(on_event=lambda k, e: ev2.append((k, e)))
        starts = [e["frame"] for k, e in ev2 if k == "frame_start"]
        self.assertEqual(starts, [1, 2, 3])         # 从头来
        self.assertTrue(r["ok"])


class TestFailureModes(_Base):
    def test_missing_blend(self):
        cfg = JobConfig(blend=os.path.join(self.dir, "nope.blend"), frames=[1],
                        output_template=self.out, state_path=self.state)
        job = RenderJob(cfg, sys.executable, cmd_factory=fake_factory)
        r = job.run()
        self.assertFalse(r["ok"])
        self.assertIn("找不到工程文件", r["error"])

    def test_missing_blender_exe(self):
        cfg = JobConfig(blend=self.blend, frames=[1], output_template=self.out,
                        state_path=self.state)
        job = RenderJob(cfg, os.path.join(self.dir, "no-blender.exe"),
                        cmd_factory=fake_factory)
        r = job.run()
        self.assertFalse(r["ok"])
        self.assertIn("找不到 blender.exe", r["error"])

    def test_broken_progress_line_does_not_kill_job(self):
        """进度行坏了要警告，但不能把整个任务搞崩。"""
        seen = {}

        def broken_factory(blender_exe, blend, driver_path, job_json, extra_args=()):
            return [blender_exe, "-c",
                    "import sys;sys.stdout.write('##PROG##{oops\\n');sys.stdout.flush()"]
        cfg = JobConfig(blend=self.blend, frames=[1], output_template=self.out,
                        state_path=self.state)
        job = RenderJob(cfg, sys.executable, cmd_factory=broken_factory)
        r = job.run(on_event=lambda k, e: seen.setdefault(k, []).append(e))
        self.assertIn("warn", seen)
        self.assertIsNotNone(r)

    def test_cancel_mid_run(self):
        # 子进程启动要 ~0.4s，cancel 得晚一点，否则会在第一帧开始前就被取消
        job, cfg, events = self.make_job(frames=(1, 2, 3, 4, 5, 6),
                                         extra=["--sleep", "0.5"])
        t = threading.Timer(1.3, job.cancel)
        t.start()
        try:
            r = self.run_job(job, events)
        finally:
            t.cancel()
        self.assertTrue(r["cancelled"] or not r["ok"])
        self.assertLess(len(r["done"]), 6)          # 没跑完
        st = JobState.load(self.state)
        self.assertIsNotNone(st)                    # 进度仍在，下次可续
        self.assertGreaterEqual(len(st.done), 1)


class TestRestartPolicy(_Base):
    """「最多重启几次」与「一直重启直到渲完」两种策略。"""

    def test_unlimited_keeps_going_until_done(self):
        """前 3 轮都在第 3 帧崩，第 4 轮正常 —— 无限重启应该跑到底。"""
        # 单帧尝试次数也要放开：否则帧额度会先耗尽，「一直重启」形同虚设
        job, cfg, events = self.make_job(
            extra=["--fail-at", "3", "--fail-runs", "3"],
            max_restarts=-1, max_no_progress_rounds=0, max_frame_attempts=0)
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["done"], [1, 2, 3, 4])
        self.assertGreaterEqual(r["restarts"], 3)
        self.assertEqual(len(self.produced()), 4)

    def test_limited_restarts_still_stops(self):
        """同样的崩法，但上限设 1 次 —— 到点就停，剩余帧留着下次续跑。"""
        job, cfg, events = self.make_job(
            extra=["--fail-at", "3", "--fail-runs", "3"],
            max_restarts=1, max_no_progress_rounds=0)
        r = self.run_job(job, events)
        self.assertFalse(r["ok"])
        self.assertEqual(r["restarts"], 2)          # 崩到第 2 次时已超上限
        self.assertTrue(any(k == "give_up" for k, _ in events))
        # 进度仍在，下次同一条命令能续跑
        self.assertTrue(any(k == "frame_done" for k, _ in events))

    def test_no_progress_guard_stops_unlimited_mode(self):
        """一直崩在同一帧：无进展护栏必须兜住，否则「一直重启」就是死循环。"""
        job, cfg, events = self.make_job(
            extra=["--fail-at", "2"],
            max_restarts=-1, max_no_progress_rounds=2, max_frame_attempts=99)
        r = self.run_job(job, events)
        self.assertFalse(r["ok"])
        self.assertTrue(r["stopped_by_no_progress"], r)
        kinds = [k for k, _ in events]
        self.assertIn("no_progress", kinds)
        self.assertNotIn("give_up", kinds)          # 不是因为次数上限停的
        self.assertLessEqual(r["restarts"], 4)      # 确实很快停下来了

    def test_no_progress_guard_can_be_disabled(self):
        job, cfg, events = self.make_job(extra=["--fail-at", "3", "--fail-runs", "2"],
                                         max_restarts=-1, max_no_progress_rounds=0,
                                         max_frame_attempts=0)
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)

    def test_progress_resets_the_counter(self):
        """有推进就不该累计无进展轮数（每轮都完成一帧）。"""
        job, cfg, events = self.make_job(extra=["--fail-at", "3", "--fail-runs", "2"],
                                         max_restarts=-1, max_no_progress_rounds=2,
                                         max_frame_attempts=0)
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["stopped_by_no_progress"])


class TestParseRestartLimit(unittest.TestCase):
    def test_numeric(self):
        self.assertEqual(parse_restart_limit("5"), 5)
        self.assertEqual(parse_restart_limit(0), 0)
        self.assertEqual(parse_restart_limit("10"), 10)

    def test_chinese_labels_from_gui(self):
        """界面的「崩溃后重启」下拉现在直接给中文文案，解析器必须认（否则静默退回默认）。"""
        self.assertEqual(parse_restart_limit("1 次"), 1)
        self.assertEqual(parse_restart_limit("3 次"), 3)
        self.assertEqual(parse_restart_limit("5 次（默认）"), 5)
        self.assertEqual(parse_restart_limit("10 次"), 10)
        self.assertEqual(parse_restart_limit("一直重启，直到全部渲完"), UNLIMITED)

    def test_unlimited_words(self):
        for w in ("unlimited", "INFINITE", "无限", "一直", "-1", "-99", "none"):
            self.assertEqual(parse_restart_limit(w), UNLIMITED, w)

    def test_defaults(self):
        self.assertEqual(parse_restart_limit(""), 5)
        self.assertEqual(parse_restart_limit(None), 5)
        self.assertEqual(parse_restart_limit("abc"), 5)

    def test_config_flag(self):
        cfg = JobConfig(blend="a.blend", frames=[1], output_template="o/f_####",
                        max_restarts=UNLIMITED)
        self.assertTrue(cfg.unlimited_restarts)
        cfg2 = JobConfig(blend="a.blend", frames=[1], output_template="o/f_####",
                         max_restarts=5)
        self.assertFalse(cfg2.unlimited_restarts)


class TestSceneSelection(_Base):
    """多场景：场景名要一路传到 job.json（driver 靠它切场景），且换场景不能续跑。"""

    def test_scene_reaches_job_json(self):
        job, cfg, events = self.make_job(frames=(1,), scene="SceneA")
        seen = {}
        inner = job.cmd_factory

        def spy(blender_exe, blend, driver_path, job_json, extra_args=()):
            with open(job_json, "r", encoding="utf-8") as f:
                seen.update(json.load(f))
            return inner(blender_exe, blend, driver_path, job_json, extra_args)

        job.cmd_factory = spy
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)
        self.assertEqual(seen.get("scene"), "SceneA")

    def test_scene_survives_to_event_stream(self):
        """driver 的 start 里带的场景名要透传到 core 事件（界面日志靠它显示用了哪个场景）。"""
        job, cfg, events = self.make_job(frames=(1,), scene="SceneA")
        self.run_job(job, events)
        infos = [ev for k, ev in events if k == "engine_info"]
        self.assertEqual(infos[0]["scene"], "SceneA")

    def test_switching_scene_does_not_resume(self):
        """同一个工程换场景：帧号一样但内容完全是另一张图，必须从头渲染。"""
        job, cfg, ev = self.make_job(frames=(1, 2), scene="SceneA")
        self.run_job(job, ev)

        job2, cfg2, ev2 = self.make_job(frames=(1, 2), scene="SceneB")
        r = self.run_job(job2, ev2)
        warns = [e["msg"] for k, e in ev2 if k == "warn"]
        self.assertTrue(any("不匹配" in w for w in warns), warns)
        starts = [e["frame"] for k, e in ev2 if k == "frame_start"]
        self.assertEqual(starts, [1, 2])
        self.assertTrue(r["ok"])

    def test_same_scene_still_resumes(self):
        """换了场景不能续跑，但**没换**场景必须照旧能续跑（别把续跑功能一起改坏）。"""
        job, cfg, ev = self.make_job(frames=(1, 2, 3), scene="SceneA",
                                     extra=["--fail-at", "2"], max_restarts=0)
        self.run_job(job, ev)

        job2, cfg2, ev2 = self.make_job(frames=(1, 2, 3), scene="SceneA")
        self.run_job(job2, ev2)
        starts = [e["frame"] for k, e in ev2 if k == "frame_start"]
        self.assertEqual(starts, [2, 3])

    def test_state_file_records_scene(self):
        job, cfg, ev = self.make_job(frames=(1,), scene="SceneA")
        self.run_job(job, ev)
        self.assertEqual(JobState.load(self.state).scene, "SceneA")

    def test_no_scene_is_allowed(self):
        """不指定场景 = 用工程里激活的那个，配置里留 None，不能报错。"""
        job, cfg, events = self.make_job(frames=(1,))
        r = self.run_job(job, events)
        self.assertTrue(r["ok"], r)
        self.assertIsNone(cfg.scene)


class TestCmdFactory(unittest.TestCase):
    def test_python_exit_code_is_present(self):
        """没有 --python-exit-code 1，Blender 会吞掉脚本异常并返回 0（关键坑 5）。"""
        cmd = default_cmd_factory("blender.exe", "a.blend", "d.py", "j.json")
        self.assertIn("--python-exit-code", cmd)
        self.assertEqual(cmd[cmd.index("--python-exit-code") + 1], "1")
        self.assertEqual(cmd[cmd.index("--") + 1:], ["j.json"])
        # extra_args 必须在 `--` 之前，否则会被当成给脚本的参数
        cmd2 = default_cmd_factory("blender.exe", "a.blend", "d.py", "j.json",
                                   ["--cycles-device", "OPTIX"])
        self.assertLess(cmd2.index("OPTIX"), cmd2.index("--"))


class TestJobConfigArchive(unittest.TestCase):
    """JobConfig 的存档能力 —— 开机续跑要照它把整个任务重建出来。"""

    def _cfg(self, **kw):
        args = dict(blend="C:/a/house.blend", frames=[1, 2, 3],
                    output_template="C:/a/out/f_####", engine="CYCLES", samples=128,
                    device="OPTIX", resolution=(1920, 1080), resolution_percentage=50,
                    file_format="PNG", scene="SceneB", max_restarts=UNLIMITED,
                    max_frame_attempts=0, max_no_progress_rounds=7, resume=False,
                    restart_delay=1.5, extra_args=["--cycles-device", "OPTIX"])
        args.update(kw)
        return JobConfig(**args)

    def test_roundtrip_is_lossless(self):
        d = self._cfg().to_dict()
        self.assertEqual(JobConfig.from_dict(d).to_dict(), d)

    def test_roundtrip_with_defaults(self):
        d = JobConfig(blend="C:/a/b.blend", frames=[1],
                      output_template="C:/a/o/f_####").to_dict()
        self.assertEqual(JobConfig.from_dict(d).to_dict(), d)

    def test_tuple_becomes_list_for_json(self):
        """resolution 是 tuple，json 会存成 list —— 两边都得能读。"""
        d = self._cfg().to_dict()
        self.assertEqual(d["resolution"], [1920, 1080])
        self.assertEqual(JobConfig.from_dict(d).resolution, (1920, 1080))

    def test_unlimited_survives(self):
        self.assertTrue(JobConfig.from_dict(self._cfg().to_dict()).unlimited_restarts)

    def test_tolerates_missing_keys(self):
        """老版本写的存档要能读：缺字段走默认，不能抛。"""
        c = JobConfig.from_dict({"blend": "C:/a/b.blend", "frames": [1],
                                 "output_template": "C:/a/o/f_####"})
        self.assertEqual(c.max_restarts, 5)
        self.assertEqual(c.max_frame_attempts, 3)
        self.assertTrue(c.resume)

    def test_tolerates_none_and_empty(self):
        for raw in (None, {}):
            c = JobConfig.from_dict(raw)
            self.assertEqual(c.frames, [])

    def test_every_field_is_covered(self):
        """`JOB_CONFIG_FIELDS` 漏字段 = 存档悄悄丢参数。

        对着**实例属性**核，而不是对着 `to_dict()` 的输出核（那是同义反复）——
        将来给 JobConfig 加了新参数却忘了补进清单，这条测试要能红。
        """
        from brconsole.core import JOB_CONFIG_FIELDS
        cfg = self._cfg()
        self.assertEqual(set(JOB_CONFIG_FIELDS), set(vars(cfg).keys()))
        for k in JOB_CONFIG_FIELDS:
            self.assertTrue(hasattr(cfg, k), "字段名写错了：%s" % k)


class TestStateFilePath(unittest.TestCase):
    """断点文件路径的规则收在 JobConfig.state_file() 上（taskstore 也用它）。"""

    def test_default_is_beside_output(self):
        cfg = JobConfig(blend="C:/a/b.blend", frames=[1],
                        output_template="C:/a/out/f_####")
        self.assertEqual(cfg.state_file(),
                         os.path.join(os.path.abspath("C:/a/out"), ".render_state.json"))

    def test_explicit_wins(self):
        cfg = JobConfig(blend="C:/a/b.blend", frames=[1],
                        output_template="C:/a/out/f_####",
                        state_path="C:/s/p.json")
        self.assertEqual(cfg.state_file(), "C:/s/p.json")

    def test_matches_what_renderjob_writes(self):
        """和真正写盘的那份必须一致，否则 taskstore 会去错地方找进度。"""
        cfg = JobConfig(blend="C:/a/b.blend", frames=[1],
                        output_template="C:/a/out/f_####")
        tmp = tempfile.mkdtemp(prefix="brc-statefile-")
        try:
            cfg.blend = os.path.join(tmp, "b.blend")
            with open(cfg.blend, "wb") as f:
                f.write(b"BLENDER-v5")
            cfg.output_template = os.path.join(tmp, "out", "f_####")
            job = RenderJob(cfg, "blender.exe", cmd_factory=lambda *a, **k: ["x"])
            events = []
            job._init_state(lambda k, **kw: events.append((k, kw)))
            self.assertTrue(os.path.exists(cfg.state_file()))
            self.assertEqual(os.path.basename(cfg.state_file()), ".render_state.json")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
