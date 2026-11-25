# -*- coding: utf-8 -*-
"""CLI 参数层测试。要害：负数限次必须在参数层就报错 —— 内核里 `<=0` 恰好是
"不限"的约定，负数一路传下去会静默翻转语义（GUI 有钳 0 + 警告，CLI 是无人值守
脚本，必须当场报错）。"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole.cli import ConsoleReporter, build_parser  # noqa: E402
from brconsole.guimodel import (event_line, event_text, outcome_reason,  # noqa: E402
                                settle_task_action)


class TestConsoleReporterNativeFilter(unittest.TestCase):
    """CLI --verbose 与 GUI 共用折叠规则：工程贴图告警不刷屏，但留汇总账。"""

    def test_benign_warnings_are_folded_and_summarised(self):
        stream = io.StringIO()
        r = ConsoleReporter(verbose=True, stream=stream)
        for _ in range(5):
            r("native", {"line": "WARNING Image file E:\\proj\\x.png does not exist."})
        r("native", {"line": "ERROR Failed to load 2 image files"})
        self.assertNotIn("does not exist", stream.getvalue())
        r._flush_native()
        self.assertIn("已折叠 6 行工程贴图/色彩空间告警", stream.getvalue())

    def test_real_error_passes_through(self):
        stream = io.StringIO()
        r = ConsoleReporter(verbose=True, stream=stream)
        r("native", {"line": "Error: Texture not found"})
        self.assertIn("Error: Texture not found", stream.getvalue())


class TestEventTextShared(unittest.TestCase):
    """CLI 与 GUI 的事件文案必须有同一事实来源，否则改一边就对不上账。"""

    def test_crash_sentence_shared_by_both_adapters(self):
        ev = {"rc": 1, "restart_index": 2, "done": 5, "total": 12,
              "remaining": [6, 7]}
        sentence = event_text("crash", ev)
        self.assertIn(sentence, event_line("crash", ev))
        stream = io.StringIO()
        ConsoleReporter(stream=stream)("crash", ev)
        self.assertIn(sentence, stream.getvalue())

    def test_engine_info_reaches_both_adapters(self):
        """场景名此前发了事件却没人显示；现在两端都能看到。"""
        ev = {"scene": "SceneA", "engine": "CYCLES", "frames": [1, 2]}
        self.assertIn("SceneA", event_line("engine_info", ev))
        stream = io.StringIO()
        ConsoleReporter(stream=stream)("engine_info", ev)
        self.assertIn("SceneA", stream.getvalue())

    def test_job_outcome_reason_has_single_owner(self):
        ev = {"cancelled": True, "ok": False}
        self.assertEqual(outcome_reason("job_done", ev),
                         ("cancelled", "你停止了任务"))
        self.assertEqual(settle_task_action("job_done", ev),
                         ("stop", "你停止了任务"))


class TestParserValidation(unittest.TestCase):
    def test_negative_frame_attempts_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["x.blend", "-s", "1", "-e", "2", "--max-frame-attempts", "-3"])

    def test_negative_no_progress_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["x.blend", "-s", "1", "-e", "2", "--max-no-progress", "-1"])

    def test_zero_means_unlimited_is_accepted(self):
        """反向护栏：0 是合法值（= 不限），别把校验写成"只许正数"。"""
        args = build_parser().parse_args(
            ["x.blend", "-s", "1", "-e", "2",
             "--max-frame-attempts", "0", "--max-no-progress", "0"])
        self.assertEqual((args.max_frame_attempts, args.max_no_progress), (0, 0))

    def test_positive_defaults(self):
        args = build_parser().parse_args(["x.blend"])
        self.assertEqual((args.max_frame_attempts, args.max_no_progress), (3, 3))

    def test_hide_collection_repeats(self):
        """--hide-collection 可重复；顺序保留（driver 按给出的顺序处理）。"""
        args = build_parser().parse_args(
            ["x.blend", "--hide-collection", "泡泡系统", "--hide-collection", "04_动态"])
        self.assertEqual(args.hidden_collections, ["泡泡系统", "04_动态"])


class TestHiddenCollectionsField(unittest.TestCase):
    """隐藏集合字段从配置 → 存档 → 签名一路不丢（AGENTS 坑 56：字段死在生产侧）。"""

    def test_roundtrip_through_config(self):
        from brconsole.core import JobConfig
        cfg = JobConfig("x.blend", [1, 2], "out/f_####",
                        hidden_collections=["泡泡系统", "04_动态"])
        d = cfg.to_dict()
        self.assertEqual(d["hidden_collections"], ["泡泡系统", "04_动态"])
        rebuilt = JobConfig.from_dict(d)
        self.assertEqual(rebuilt.hidden_collections, ["泡泡系统", "04_动态"])

    def test_old_config_defaults_to_empty(self):
        from brconsole.core import JobConfig
        cfg = JobConfig.from_dict({"blend": "x.blend", "frames": [1], "output_template": "o"})
        self.assertEqual(cfg.hidden_collections, [])

    def test_signature_changes_with_hidden_collections(self):
        from brconsole.core import JobConfig
        a = JobConfig("x.blend", [1], "out/f_####")
        b = JobConfig("x.blend", [1], "out/f_####", hidden_collections=["04_动态"])
        self.assertNotEqual(a.signature(), b.signature())
        # 顺序无关（界面勾选顺序不该影响任务身份）
        c = JobConfig("x.blend", [1], "out/f_####",
                      hidden_collections=["04_动态", "泡泡系统"])
        d = JobConfig("x.blend", [1], "out/f_####",
                      hidden_collections=["泡泡系统", "04_动态"])
        self.assertEqual(c.signature(), d.signature())


class TestFriendlyFrameErrors(unittest.TestCase):
    """帧范围写错时：报错是人话（指出第几段 / 步长必须 ≥ 1），**不能变成 traceback**。

    `parse_frames` 现在抛的是面向用户的中文 ValueError —— 但 `main` 若不接，
    用户看到的就是一整屏堆栈 + 最后一行的原文。这组用例钉住"接住了"这件事。
    """

    def setUp(self):
        import shutil
        import tempfile
        self.dir = tempfile.mkdtemp(prefix=".brc-cli-")
        self._shutil = shutil
        self.blend = os.path.join(self.dir, "x.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER-v5")

    def tearDown(self):
        self._shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, argv):
        import contextlib
        import io
        from brconsole.cli import main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([self.blend] + argv)
        return rc, buf.getvalue()

    def test_bad_spec_names_the_segment(self):
        rc, out = self._run(["-f", "1-2,abc"])
        self.assertEqual(rc, 2)
        self.assertIn("第 2 段", out)
        self.assertIn("abc", out)
        self.assertNotIn("Traceback", out)

    def test_zero_step_is_reported(self):
        """--step 0 不能变成 traceback，也不能被静默当成 1。"""
        rc, out = self._run(["-s", "1", "-e", "10", "--step", "0"])
        self.assertEqual(rc, 2)
        self.assertIn("步长", out)
        self.assertNotIn("Traceback", out)

    def test_fullwidth_comma_is_accepted(self):
        """中文输入法随手打的全角逗号 → 帧解析通过。

        ⚠️ 用一条不存在的 `--blender` 路径把流程**钉死在"走到了 Blender 解析"**：
        不这么做的话，装了 Blender 的机器上这条用例会真的开渲（依赖外部环境、还慢）。
        判据：走到 Blender 那一步（"指定的 blender.exe 不存在"），就说明帧解析没报错。
        """
        rc, out = self._run(["-f", "1-2，4", "--blender",
                             os.path.join(self.dir, "no-such.exe")])
        self.assertEqual(rc, 2)
        self.assertNotIn("帧范围不对", out)
        self.assertIn("指定的 blender.exe 不存在", out)



if __name__ == "__main__":
    unittest.main()
