# -*- coding: utf-8 -*-
"""工程配置读取的解析与路径换算测试（不启动 Blender，真机读由冒烟脚本覆盖）。"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from brconsole.inspect import MARK, output_template_from, parse_info, summarize  # noqa: E402


class TestParseInfo(unittest.TestCase):
    def test_picks_marked_line(self):
        out = ("Blender 5.2.2 LTS\n"
               "Read blend: x.blend\n"
               '%s{"engine": "CYCLES", "samples": 128}\n'
               "Blender quit\n") % MARK
        info = parse_info(out)
        self.assertEqual(info["engine"], "CYCLES")
        self.assertEqual(info["samples"], 128)

    def test_survives_noise_after(self):
        """Blender 在脚本之后还会吐别的行（例如 Blender quit），得能捞出来。"""
        out = "%s{\"ok\": true, \"frame_start\": 1}\nSaved: 'x.png'\nBlender quit\n" % MARK
        self.assertEqual(parse_info(out)["frame_start"], 1)

    def test_carriage_returns(self):
        out = "junk\r%s{\"ok\": true}\rmore" % MARK
        self.assertTrue(parse_info(out)["ok"])

    def test_missing_marker(self):
        self.assertIsNone(parse_info("nothing here"))
        self.assertIsNone(parse_info(""))

    def test_broken_json(self):
        self.assertIsNone(parse_info("%s{oops" % MARK))


class TestOutputTemplate(unittest.TestCase):
    def test_directory(self):
        out = output_template_from("D:/renders/")
        self.assertEqual(out.replace("\\", "/"), "D:/renders/frame_####")

    def test_prefix(self):
        out = output_template_from("D:/renders/shot_")
        self.assertEqual(out.replace("\\", "/"), "D:/renders/shot_####")

    def test_prefix_with_extension(self):
        out = output_template_from("D:/renders/shot_.png")
        self.assertEqual(out.replace("\\", "/"), "D:/renders/shot_####.png")

    def test_plain_name(self):
        out = output_template_from("D:/renders/out")
        self.assertEqual(out.replace("\\", "/"), "D:/renders/out_####")

    def test_keeps_existing_placeholder(self):
        self.assertEqual(output_template_from("D:/r/f_####.png"), "D:/r/f_####.png")
        self.assertEqual(output_template_from("D:/r/f_####"), "D:/r/f_####")

    def test_blender_default_tmp_means_unset(self):
        """Blender 的默认输出路径是 /tmp/，不能把它当真实目录填进表单。"""
        out = output_template_from("/tmp\\", "D:/proj/scene.blend")
        self.assertTrue(out.replace("\\", "/").endswith("proj/scene_####"), out)
        self.assertEqual(output_template_from("/tmp/"), "")

    def test_empty_falls_back_to_blend_name(self):
        out = output_template_from("", "D:/proj/scene.blend")
        self.assertTrue(out.replace("\\", "/").endswith("proj/scene_####"), out)

    def test_empty_everything(self):
        self.assertEqual(output_template_from("", ""), "")


class TestSummarize(unittest.TestCase):
    def test_ok(self):
        s = summarize({"ok": True, "blend": "D:/a/shot.blend", "scene": "Scene",
                       "engine": "CYCLES", "resolution": [1920, 1080],
                       "resolution_percentage": 100, "samples": 256,
                       "frame_start": 1, "frame_end": 240, "frame_step": 1,
                       "output_path": "D:/a/out_"})
        self.assertIn("CYCLES", s)
        self.assertIn("1920x1080", s)
        self.assertIn("1-240", s)

    def test_failure(self):
        self.assertIn("读取失败", summarize({"ok": False, "error": "超时"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
