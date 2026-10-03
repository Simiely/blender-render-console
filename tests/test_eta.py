# -*- coding: utf-8 -*-
"""ETA 估算器的行为测试。核心诉求：首帧预热不能污染基线。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole.eta import EtaEstimator, fmt_duration  # noqa: E402


class TestEta(unittest.TestCase):
    def test_first_frame_is_warmup(self):
        """首帧 10s 是 kernel 编译，绝不能进基线（实测 EEVEE 首帧 10.25s、后续 ~0.1s）。"""
        e = EtaEstimator()
        self.assertFalse(e.add(10.25))        # 返回 False = 被当作预热剔除
        self.assertTrue(e.add(0.12))
        self.assertTrue(e.add(0.10))
        self.assertAlmostEqual(e.warmups, [10.25])
        self.assertAlmostEqual(e.per_frame, 0.11, places=2)

    def test_no_baseline_before_samples(self):
        e = EtaEstimator()
        e.add(10.0)                            # 只有预热帧
        self.assertIsNone(e.estimate(10))
        self.assertEqual(e.mode, "尚无样本")

    def test_small_sample_falls_back_to_mean(self):
        """样本 ≤2 时 EMA 还没收敛，用均值更诚实。"""
        e = EtaEstimator(alpha=0.3)
        e.add(99.0, warmup=True)
        e.add(2.0)
        e.add(4.0)
        self.assertEqual(e.mode, "均值（样本 2）")
        self.assertAlmostEqual(e.per_frame, 3.0, places=6)

    def test_ema_smooths_jitter(self):
        """一个尖峰不该把 ETA 拉飞。"""
        steady = EtaEstimator()
        for _ in range(5):
            steady.add(1.0)
        base = steady.per_frame

        spiky = EtaEstimator()
        for _ in range(5):
            spiky.add(1.0)
        spiky.add(20.0)
        self.assertLess(spiky.per_frame, 8.0)     # 尖峰被压住
        self.assertGreater(spiky.per_frame, base)

    def test_estimate_scales_with_remaining(self):
        e = EtaEstimator()
        for _ in range(6):
            e.add(2.0)
        self.assertAlmostEqual(e.estimate(10), e.per_frame * 10, places=6)
        self.assertEqual(e.estimate(0), 0.0)

    def test_explicit_warmup_flag(self):
        """续跑时每轮重启都要重新预热（Blender 重启会重新编译 kernel）。"""
        e = EtaEstimator()
        e.add(1.0, warmup=False)
        e.add(8.0, warmup=True)                  # 第二轮的首帧
        self.assertEqual(len(e.samples), 1)
        self.assertIn(8.0, e.warmups)

    def test_snapshot_fields(self):
        e = EtaEstimator()
        e.add(5.0)
        e.add(1.0)
        e.add(2.0)
        snap = e.snapshot(7)
        self.assertEqual(snap["remaining_frames"], 7)
        self.assertIsNotNone(snap["eta_sec"])
        self.assertEqual(snap["n_samples"], 2)
        self.assertEqual(snap["warmup_secs"], 5.0)
        self.assertAlmostEqual(snap["median"], 1.5, places=6)

    def test_fmt_duration(self):
        self.assertEqual(fmt_duration(None), "--")
        self.assertEqual(fmt_duration(3.5), "3.5s")
        self.assertEqual(fmt_duration(65.0), "01m05s")
        self.assertEqual(fmt_duration(3725.0), "1h02m05s")
        self.assertEqual(fmt_duration(-1), "0.0s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
