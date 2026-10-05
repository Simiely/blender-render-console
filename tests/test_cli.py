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


if __name__ == "__main__":
    unittest.main()
