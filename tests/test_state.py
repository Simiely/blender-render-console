# -*- coding: utf-8 -*-
"""断点状态文件测试。要害：原子落盘 + 「还剩哪些帧」的口径。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole.state import JobState  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=".brc-test-")
        self.path = os.path.join(self.dir, "state.json")

    def tearDown(self):
        try:
            shutil.rmtree(self.dir)
        except Exception:
            pass


class TestState(_Base):
    def test_roundtrip(self):
        st = JobState(self.path, blend="a.blend", frames=[1, 2, 3], output_template="o/####")
        st.mark_done(1, secs=1.5, path="o/0001.png")
        st.mark_failed(2, err="boom")
        st.note_attempt(3)
        st.save()

        back = JobState.load(self.path)
        self.assertEqual(back.frames, [1, 2, 3])
        self.assertIn(1, back.done)            # JSON 的字符串键要还原成 int
        self.assertEqual(back.done[1]["secs"], 1.5)
        self.assertIn(2, back.failed)
        self.assertEqual(back.attempts[3], 1)

    def test_remaining_excludes_done_failed_and_exhausted(self):
        st = JobState(self.path, frames=[1, 2, 3, 4])
        st.mark_done(1)
        st.mark_failed(2)
        st.note_attempt(3)
        st.note_attempt(3)
        st.note_attempt(3)                      # 达到 max_attempts=3
        self.assertEqual(st.remaining(3), [4])
        self.assertEqual(st.exhausted(3), [3])

    def test_attempt_quota_respected(self):
        st = JobState(self.path, frames=[1])
        st.note_attempt(1)
        self.assertEqual(st.remaining(1), [])   # 用掉唯一一次尝试
        self.assertEqual(st.exhausted(1), [1])

    def test_done_overrides_failed(self):
        """重试成功：把之前记的失败抹掉，否则界面会同时显示完成和失败。"""
        st = JobState(self.path, frames=[1])
        st.mark_failed(1, err="crash")
        st.mark_done(1, secs=2.0, path="x.png")
        self.assertNotIn(1, st.failed)
        self.assertTrue(st.is_complete())

    def test_atomic_save_leaves_no_tmp(self):
        st = JobState(self.path, frames=[1])
        st.mark_done(1)
        st.save()
        leftovers = [f for f in os.listdir(self.dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_corrupted_state_loads_as_none(self):
        """状态文件写坏时宁可从头来，也不要让程序起不来。"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(JobState.load(self.path))

    def test_missing_state(self):
        self.assertIsNone(JobState.load(os.path.join(self.dir, "nope.json")))

    def test_is_complete_and_stats(self):
        st = JobState(self.path, frames=[1, 2])
        st.mark_done(1, secs=2.0)
        self.assertFalse(st.is_complete())
        st.mark_done(2, secs=4.0)
        self.assertTrue(st.is_complete())
        s = st.stats()
        self.assertEqual(s["done"], 2)
        self.assertEqual(s["total"], 2)
        self.assertAlmostEqual(s["sum"], 6.0)
        self.assertEqual(s["min"], 2.0)

    def test_saved_json_is_sorted_and_readable(self):
        st = JobState(self.path, frames=[3, 1, 2])
        for f in (3, 1, 2):
            st.mark_done(f, secs=1.0)
        st.save()
        with open(self.path, "r", encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(list(d["done"].keys()), ["1", "2", "3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
