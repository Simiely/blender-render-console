# -*- coding: utf-8 -*-
"""原生输出解析测试。样本全部来自 probes/*.log（Blender 5.2.2 LTS 实测）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole.parser import NativeParser, parse_stage, parse_stamp  # noqa: E402

# 直接从真实日志里摘出来的行，别手敲 —— 手敲会悄悄改格式
CYCLES_SAMPLE = "00:03.906  render           | Fra: 4 | Mem: 6M | Sample 24/24"
CYCLES_REMAINING = "00:03.906  render | Fra: 4 | Remaining: 00:00.04 | Mem: 6M | Sample 1/24"
EEVEE_SAMPLE = "00:11.765  render           | Fra: 1 | Rendering 1 / 64 samples"
SAVED = r"""00:04.094  render | Saved: 'C:\out\probe_0004.png'"""
BVH = "00:02.453  render | Fra: 1 | Mem: 1M | Updating Scene BVH | Building BVH 0%"
KERNEL = "00:02.437  render | Fra: 1 | Mem: 0M | Loading render kernels (may take a few minutes the first time)"
TIME = "00:12.187  render           | Time: 00:10.25 (Saving: 00:00.11)"
# 网上流传的 4.x 格式 —— 在本项目里是「不该出现」的反例
LEGACY_4X = "Fra:1 Mem:1234.56M (Peak 1500.00M) | Time:00:01.23 | Remaining:04:32.10 | Scene, View Layer | Sample 64/128"


class TestParser(unittest.TestCase):
    def test_cycles_sample(self):
        p = NativeParser()
        out = p.feed(CYCLES_SAMPLE)
        self.assertEqual(out["frame"], 4)
        self.assertEqual(out["sample"], (24, 24))

    def test_eevee_sample(self):
        p = NativeParser()
        out = p.feed(EEVEE_SAMPLE)
        self.assertEqual(out["frame"], 1)
        self.assertEqual(out["sample"], (1, 64))

    def test_saved(self):
        p = NativeParser()
        out = p.feed(SAVED)
        self.assertTrue(out["saved"].endswith("probe_0004.png"))

    def test_remaining_is_frame_local(self):
        """Remaining 只作展示，语义是帧内剩余（不参与 ETA —— 那是 eta.py 的事）。"""
        p = NativeParser()
        out = p.feed(CYCLES_REMAINING)
        self.assertEqual(out["remaining_in_frame"], "00:00.04")

    def test_phase_detection(self):
        self.assertEqual(parse_stage(KERNEL), "正在编译着色器 / 加载渲染 kernel（首次可能数分钟）")
        self.assertEqual(parse_stage(BVH), "正在构建 BVH")
        self.assertIsNone(parse_stage(CYCLES_SAMPLE))

    def test_phase_emitted_once(self):
        p = NativeParser()
        self.assertIn("phase", p.feed(BVH))
        self.assertIsNone(p.feed(BVH))          # 同阶段不重复刷屏

    def test_stamp(self):
        self.assertEqual(parse_stamp(CYCLES_SAMPLE)[0], 3.906)
        self.assertEqual(parse_stamp(CYCLES_SAMPLE)[1], "render")
        self.assertIsNone(parse_stamp("not a stamped line"))

    def test_legacy_4x_line_still_degrades_gracefully(self):
        """4.x 格式万一出现也不能崩；但它不该被当成 5.2 的正常进度。"""
        p = NativeParser()
        out = p.feed(LEGACY_4X)
        self.assertEqual(out["frame"], 1)
        self.assertEqual(out["sample"], (64, 128))

    def test_time_line_is_not_a_phase(self):
        p = NativeParser()
        out = p.feed(TIME)
        self.assertNotIn("phase", out or {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
