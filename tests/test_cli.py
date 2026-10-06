# -*- coding: utf-8 -*-
"""CLI 参数层测试。要害：负数限次必须在参数层就报错 —— 内核里 `<=0` 恰好是
"不限"的约定，负数一路传下去会静默翻转语义（GUI 有钳 0 + 警告，CLI 是无人值守
脚本，必须当场报错）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole.cli import build_parser  # noqa: E402


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
