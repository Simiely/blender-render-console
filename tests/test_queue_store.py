# -*- coding: utf-8 -*-
"""多工程队列存储层测试（不碰 GUI、不跑 Blender）。

用临时目录伪造 LOCALAPPDATA，避免污染真实 pending.json / queue_store.json。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from brconsole import taskstore, queue_store           # noqa: E402
from brconsole.core import JobConfig             # noqa: E402


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=".brc-queue-test-")
        self._old_env = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.dir

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def cfg(self, name):
        return JobConfig(os.path.join(self.dir, name + ".blend"), [1, 2],
                         os.path.join(self.dir, name, "f_####"))

    def test_add_and_load(self):
        q, item = queue_store.add(self.cfg("a"), "blender.exe")
        self.assertEqual(len(q["items"]), 1)
        loaded = queue_store.load()
        self.assertEqual(loaded["items"][0]["config"]["blend"], item["config"]["blend"])
        self.assertEqual(item["status"], queue_store.STATUS_PENDING)

    def test_remove_and_empty_clears_file(self):
        q, item = queue_store.add(self.cfg("a"), "b")
        q2, _ = queue_store.add(self.cfg("b"), "b")
        self.assertTrue(queue_store.remove(item["id"]))
        self.assertEqual(len(queue_store.load()["items"]), 1)
        self.assertTrue(queue_store.remove(q2["items"][1]["id"]))
        self.assertIsNone(queue_store.load())            # 空队列 → 文件删掉
        self.assertFalse(os.path.exists(queue_store.queue_path()))

    def test_broken_file_is_none(self):
        os.makedirs(os.path.dirname(queue_store.queue_path()), exist_ok=True)
        with open(queue_store.queue_path(), "w", encoding="utf-8") as f:
            f.write("not json")
        self.assertIsNone(queue_store.load())

    def test_wrong_version_is_none(self):
        queue_store._atomic_write({"version": 999, "items": []}, queue_store.queue_path())
        self.assertIsNone(queue_store.load())

    def test_move_reorders_and_resets_current(self):
        a, _ = queue_store.add(self.cfg("a"), "b")
        b2, _ = queue_store.add(self.cfg("b"), "b")
        self.assertTrue(queue_store.move(a["items"][0]["id"], +1))
        names = [it["config"]["blend"] for it in queue_store.load()["items"]]
        self.assertTrue(names[0].endswith("b.blend"))
        self.assertEqual(queue_store.load()["current"], 0)

    def test_move_out_of_bounds_is_noop(self):
        q, item = queue_store.add(self.cfg("a"), "b")
        self.assertFalse(queue_store.move(item["id"], -1))
        self.assertFalse(queue_store.move(item["id"], +1))

    def test_status_flow(self):
        q, item = queue_store.add(self.cfg("a"), "b")
        self.assertTrue(queue_store.set_status(item["id"], queue_store.STATUS_RUNNING))
        self.assertEqual(queue_store.load()["items"][0]["status"], queue_store.STATUS_RUNNING)
        self.assertTrue(queue_store.set_status(item["id"], queue_store.STATUS_DONE))
        self.assertEqual(queue_store.load()["items"][0]["status"], queue_store.STATUS_DONE)

    def test_set_current_writes_pending_for_autostart(self):
        """关键契约：队列项设为当前时，必须写 pending.json，否则开机续跑认不到。"""
        q, item = queue_store.add(self.cfg("a"), "blender.exe")
        path = queue_store.set_current(item["id"])
        self.assertTrue(path)
        rec = taskstore.load()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["blender_exe"], "blender.exe")
        self.assertEqual(rec["config"]["blend"], item["config"]["blend"])

    def test_next_pending_skips_done(self):
        q, a = queue_store.add(self.cfg("a"), "b")
        q2, b2 = queue_store.add(self.cfg("b"), "b")
        queue_store.set_status(a["id"], queue_store.STATUS_DONE)
        nxt = queue_store.next_pending()
        self.assertEqual(nxt["id"], b2["id"])

    def test_summary(self):
        q, a = queue_store.add(self.cfg("a"), "b")
        queue_store.add(self.cfg("b"), "b")
        queue_store.set_status(a["id"], queue_store.STATUS_DONE)
        s = queue_store.summary()
        self.assertIn("共 2 项", s)
        self.assertIn("完成 1", s)

    def test_old_pending_untouched_by_queue(self):
        """队列不能破坏旧的单任务存档（向后兼容）。"""
        cfg = self.cfg("legacy")
        taskstore.save(cfg, "blender.exe")
        queue_store.add(self.cfg("a"), "b")
        rec = taskstore.load()
        self.assertEqual(rec["config"]["blend"], os.path.abspath(cfg.blend))


if __name__ == "__main__":
    unittest.main()
