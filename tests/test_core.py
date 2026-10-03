# -*- coding: utf-8 -*-
"""调度核心的端到端测试。

不启动真 Blender（另见 tools/smoke_real_blender.py 做真机实测），
而是让 `fake_blender.py` 输出**与真机同构**的行（原生行用 \\r 刷新 + ##PROG## JSON），
这样子进程管理、双通道解析、崩溃续跑、取消这些**接线**都能在 CI 里测到 ——
只测 service 层是测不出接线断没断的。
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from brconsole.core import JobConfig, RenderJob, default_cmd_factory  # noqa: E402
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
