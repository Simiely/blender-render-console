# -*- coding: utf-8 -*-
"""待办任务存档的测试。

**绝不碰真实的 `%LOCALAPPDATA%`**：每个用例把 `LOCALAPPDATA` 指到临时目录，
所以这里的存档操作全都落在沙箱里，跑完就删。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from brconsole import taskstore                                    # noqa: E402
from brconsole.core import JobConfig, UNLIMITED                    # noqa: E402
from brconsole.state import JobState                               # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("LOCALAPPDATA")
        self.tmp = tempfile.mkdtemp(prefix="brc-taskstore-")
        # 临时 LOCALAPPDATA 下再套一层，顺便验证 store_dir 会长出子目录
        os.environ["LOCALAPPDATA"] = os.path.join(self.tmp, "Local")
        self.blend = os.path.join(self.tmp, "house.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER-v5")
        self.blender = os.path.join(self.tmp, "blender.exe")
        with open(self.blender, "wb") as f:
            f.write(b"MZ")
        self.out = os.path.join(self.tmp, "out", "frame_####")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, **kw):
        args = dict(blend=self.blend, frames=[1, 2, 3, 4], output_template=self.out,
                    scene="SceneB", max_restarts=UNLIMITED, max_frame_attempts=0,
                    max_no_progress_rounds=7)
        args.update(kw)
        return JobConfig(**args)

    def read_raw(self):
        """直接读写存档原文（用来伪造"坏存档"）。"""
        with open(taskstore.pending_path(), encoding="utf-8") as f:
            return json.load(f)

    def write_raw(self, raw):
        with open(taskstore.pending_path(), "w", encoding="utf-8") as f:
            json.dump(raw, f)

    def write_state(self, cfg=None, done=(), failed=()):
        """模拟「跑过一部分」：直接写断点文件，不跑渲染。"""
        cfg = cfg or self.cfg()
        st = JobState(cfg.state_file(), blend=cfg.blend, frames=cfg.frames,
                      output_template=cfg.output_template, scene=cfg.scene)
        for f in done:
            st.mark_done(f, secs=1.0, path="x_%d.png" % f)
        for f in failed:
            st.mark_failed(f, "boom")
        st.save()
        return st


class TestStore(Base):
    def test_dir_is_under_localappdata(self):
        self.assertEqual(taskstore.store_dir(),
                         os.path.join(os.environ["LOCALAPPDATA"],
                                      "blender-render-console"))
        self.assertTrue(taskstore.pending_path().endswith("pending.json"))

    def test_roundtrip(self):
        cfg = self.cfg()
        p = taskstore.save(cfg, self.blender, autoresume=True)
        self.assertTrue(os.path.exists(p))
        rec = taskstore.load()
        self.assertEqual(rec["blender_exe"], self.blender)
        self.assertTrue(rec["autoresume"])
        back = taskstore.config_of(rec)
        self.assertEqual(back.to_dict(), cfg.to_dict())

    def test_only_one_task_kept(self):
        """覆盖是**有意的**：渲染是独占 GPU 的重活，只保留"当前任务"。"""
        taskstore.save(self.cfg(frames=[1, 2]), self.blender)
        taskstore.save(self.cfg(frames=[9, 10]), self.blender)
        self.assertEqual(taskstore.config_of(taskstore.load()).frames, [9, 10])

    def test_missing_returns_none(self):
        self.assertIsNone(taskstore.load())

    def test_corrupt_returns_none(self):
        os.makedirs(taskstore.store_dir(), exist_ok=True)
        with open(taskstore.pending_path(), "w", encoding="utf-8") as f:
            f.write("{不是 JSON")
        self.assertIsNone(taskstore.load())

    def test_wrong_version_returns_none(self):
        taskstore.save(self.cfg(), self.blender)
        raw = self.read_raw()
        raw["version"] = 999
        self.write_raw(raw)
        self.assertIsNone(taskstore.load())

    def test_no_tmp_left_behind(self):
        taskstore.save(self.cfg(), self.blender)
        leftovers = [n for n in os.listdir(taskstore.store_dir()) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_clear(self):
        taskstore.save(self.cfg(), self.blender)
        self.assertTrue(taskstore.clear())
        self.assertIsNone(taskstore.load())
        self.assertFalse(taskstore.clear())          # 已经没了，返回 False 而不是抛


class TestAutoresumeFlag(Base):
    def test_set_false_and_back(self):
        taskstore.save(self.cfg(), self.blender, autoresume=True)
        self.assertTrue(taskstore.set_autoresume(False))
        self.assertFalse(taskstore.load()["autoresume"])
        self.assertTrue(taskstore.set_autoresume(True))
        self.assertTrue(taskstore.load()["autoresume"])

    def test_set_without_archive_is_false(self):
        self.assertFalse(taskstore.set_autoresume(False))

    def test_setting_does_not_touch_config(self):
        """只改标记、不重写 config：用户改了界面参数但还没点开始时，
        存档里仍应是"上次真正跑的那个任务"。"""
        cfg = self.cfg(frames=[1, 2, 3])
        taskstore.save(cfg, self.blender)
        taskstore.set_autoresume(False)
        self.assertEqual(taskstore.config_of(taskstore.load()).to_dict(), cfg.to_dict())

    def test_reason_recorded_and_cleared(self):
        """开机那一步**没人在旁边看着**，日志必须能说清"为什么这次不自动跑"。"""
        taskstore.save(self.cfg(), self.blender, autoresume=True)
        taskstore.set_autoresume(False, "你点了「停止」")
        self.assertEqual(taskstore.load()["auto_reason"], "你点了「停止」")
        taskstore.set_autoresume(True)
        self.assertEqual(taskstore.load()["auto_reason"], "")     # 重新开启就别留旧原因


class TestDecision(Base):
    def test_no_archive(self):
        self.assertEqual(taskstore.resume_decision()[0], "none")

    def test_prompt_carries_the_reason(self):
        """prompt 必须带上原因 —— 用户开机只看到一行日志，得知道是谁关掉的自动续跑。"""
        cfg = self.cfg()
        taskstore.save(cfg, self.blender, autoresume=True)
        self.write_state(cfg, done=[1])
        taskstore.set_autoresume(False, "上次运行没能跑完")
        action, why = taskstore.resume_decision()
        self.assertEqual(action, "prompt")
        self.assertEqual(why, "上次运行没能跑完")

    def test_save_records_reason_when_auto_is_off(self):
        taskstore.save(self.cfg(), self.blender, autoresume=False,
                       reason="你在界面上关掉了自动续跑")
        action, why = taskstore.resume_decision()
        self.assertEqual(action, "prompt")
        self.assertEqual(why, "你在界面上关掉了自动续跑")

    def test_fresh_task_runs(self):
        """核心承诺：点了开始、存了档，然后进程被强杀（断点不完整）→ 开机直接续跑。"""
        taskstore.save(self.cfg(), self.blender, autoresume=True)
        action, why = taskstore.resume_decision()
        self.assertEqual(action, "run", why)

    def test_partial_progress_still_runs(self):
        cfg = self.cfg(frames=[1, 2, 3, 4])
        taskstore.save(cfg, self.blender, autoresume=True)
        self.write_state(cfg, done=[1, 2])
        action, why = taskstore.resume_decision()
        self.assertEqual(action, "run", why)
        self.assertEqual(taskstore.progress_of(cfg)["done"], 2)

    def test_complete_is_done(self):
        cfg = self.cfg(frames=[1, 2])
        taskstore.save(cfg, self.blender, autoresume=True)
        self.write_state(cfg, done=[1, 2])
        self.assertEqual(taskstore.resume_decision()[0], "done")

    def test_user_stopped_prompts(self):
        """用户主动点过停止 → 不自动跑，但**要在界面上提示**（prompt 而不是 none）。"""
        cfg = self.cfg()
        taskstore.save(cfg, self.blender, autoresume=True)
        self.write_state(cfg, done=[1])
        taskstore.set_autoresume(False)
        self.assertEqual(taskstore.resume_decision()[0], "prompt")

    def test_missing_blender_drops_but_keeps_archive(self):
        """blender 位置变了是可恢复的，**不能删档**，否则这次任务再也找不回来。"""
        taskstore.save(self.cfg(), os.path.join(self.tmp, "gone.exe"))
        action, why = taskstore.resume_decision()
        self.assertEqual(action, "drop")
        self.assertIn("blender.exe", why)
        self.assertIsNotNone(taskstore.load())       # 存档还在

    def test_missing_blend_drops_but_keeps_archive(self):
        cfg = self.cfg()
        taskstore.save(cfg, self.blender)
        os.remove(self.blend)
        self.assertEqual(taskstore.resume_decision()[0], "drop")
        self.assertIsNotNone(taskstore.load())

    def test_broken_config_drops(self):
        taskstore.save(self.cfg(), self.blender)
        raw = self.read_raw()
        raw["config"] = {"blend": "", "frames": []}       # 缺关键字段
        self.write_raw(raw)
        self.assertEqual(taskstore.resume_decision()[0], "drop")


class TestProgress(Base):
    def test_no_state_file(self):
        self.assertIsNone(taskstore.progress_of(self.cfg()))

    def test_state_of_other_task_is_ignored(self):
        """断点属于别的任务（换了帧范围/场景）→ 对我们等于"还没开始"。"""
        other = self.cfg(frames=[1, 2], scene="SceneA")
        self.write_state(other, done=[1])
        self.assertIsNone(taskstore.progress_of(self.cfg(frames=[1, 2], scene="SceneB")))

    def test_reports_failed_count(self):
        cfg = self.cfg(frames=[1, 2, 3])
        self.write_state(cfg, done=[1], failed=[2])
        p = taskstore.progress_of(cfg)
        self.assertEqual((p["done"], p["total"], p["failed"], p["complete"]),
                         (1, 3, 1, False))


class TestCheckRunnable(Base):
    # 磁盘余量一律注入：拿机器的真实剩余空间当断言条件，换台机器就会红。
    ROOMY = staticmethod(lambda folder: 500 * 1024 ** 3)
    TIGHT = staticmethod(lambda folder: 100 * 1024 ** 2)

    def test_ok(self):
        self.assertEqual(
            taskstore.check_runnable(self.cfg(), self.blender, free_fn=self.ROOMY),
            (True, ""))

    def test_no_hash_placeholder(self):
        ok, why = taskstore.check_runnable(
            self.cfg(output_template=os.path.join(self.tmp, "out", "x.png")), self.blender)
        self.assertFalse(ok)
        self.assertIn("####", why)

    def test_empty_blender(self):
        ok, why = taskstore.check_runnable(self.cfg(), "")
        self.assertFalse(ok)
        self.assertIn("blender.exe", why)

    def test_not_enough_space_blocks_resume(self):
        """开机时盘已经满了 → 不跑。这条同时也是"绝不把系统盘写满"的第一道闸。"""
        ok, why = taskstore.check_runnable(self.cfg(), self.blender, free_fn=self.TIGHT)
        self.assertFalse(ok)
        self.assertIn("可用空间", why)

    def test_space_warning_does_not_block_resume(self):
        """warn 档（"可能写不下"，但没过水位线）不该让刚开机的机器什么都不做。"""
        cfg = self.cfg(frames=list(range(1, 2001)))
        need = taskstore.diskspace.estimate_bytes(cfg.frames, cfg.resolution, None, "PNG")
        warn_free = staticmethod(lambda folder: max(need // 2, 11 * 1024 ** 3))
        ok, why = taskstore.check_runnable(cfg, self.blender, free_fn=warn_free)
        self.assertTrue(ok, why)


class TestDescribe(Base):
    def test_mentions_key_facts(self):
        cfg = self.cfg(frames=[1, 2, 3])
        self.write_state(cfg, done=[1])
        taskstore.save(cfg, self.blender)
        text = taskstore.describe(taskstore.load())
        self.assertIn("house.blend", text)
        self.assertIn("SceneB", text)
        self.assertIn("3 帧", text)
        self.assertIn("1/3", text)

    def test_not_started(self):
        taskstore.save(self.cfg(), self.blender)
        self.assertIn("尚未开始", taskstore.describe(taskstore.load()))

    def test_broken(self):
        self.assertIn("无法解析", taskstore.describe({"config": {"blend": ""}}))


if __name__ == "__main__":
    unittest.main()
