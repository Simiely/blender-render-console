# -*- coding: utf-8 -*-
"""工程配置读取的解析与路径换算测试（不启动 Blender，真机读由冒烟脚本覆盖）。"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from brconsole.inspect import (MARK, output_template_from, parse_info,  # noqa: E402
                               scene_detail, scene_names, summarize)


class TestSceneHelpers(unittest.TestCase):
    """多场景：读回来的 scene_details 是界面「场景」下拉的唯一数据源。"""

    def _multi(self):
        return {
            "ok": True, "blend": "D:/a/house.blend", "scene": "SceneB",
            "scenes": ["SceneA", "SceneB"],
            "scene_details": [
                {"name": "SceneA", "engine": "CYCLES", "resolution": [64, 48],
                 "frame_start": 1, "frame_end": 3, "camera": "cA"},
                {"name": "SceneB", "engine": "BLENDER_EEVEE", "resolution": [128, 96],
                 "frame_start": 10, "frame_end": 12, "camera": "cB"},
            ],
        }

    def test_names(self):
        self.assertEqual(scene_names(self._multi()), ["SceneA", "SceneB"])

    def test_detail_by_name(self):
        a = scene_detail(self._multi(), "SceneA")
        self.assertEqual(a["resolution"], [64, 48])
        self.assertEqual(a["frame_end"], 3)

    def test_detail_defaults_to_active_scene(self):
        """不给名字 = 取激活场景那份（Blender 打开工程时显示的那个）。"""
        self.assertEqual(scene_detail(self._multi())["name"], "SceneB")

    def test_missing_scene_returns_none(self):
        """工程里没有那个场景 → 返回 None，调用方必须明确提示，不能悄悄换场景。"""
        self.assertIsNone(scene_detail(self._multi(), "SceneC"))

    def test_legacy_result_without_details(self):
        """老脚本（或只有一份配置）的结果也要能用：退回顶层字段。"""
        info = {"ok": True, "blend": "D:/a/x.blend", "scene": "Scene",
                "engine": "CYCLES", "resolution": [10, 10]}
        self.assertEqual(scene_names(info), ["Scene"])
        self.assertEqual(scene_detail(info)["engine"], "CYCLES")
        self.assertEqual(scene_detail(info, "Scene")["engine"], "CYCLES")

    def test_failed_result_is_empty(self):
        self.assertEqual(scene_names({"ok": False, "error": "超时"}), [])
        self.assertIsNone(scene_detail({"ok": False}))
        self.assertEqual(scene_names(None), [])


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

    def test_double_slash_is_relative_to_blend(self):
        """`//` = 相对 .blend 所在目录（Blender 输出路径的默认写法），不能当无效路径丢掉。"""
        out = output_template_from("//render/shot_", "D:/proj/house.blend")
        self.assertEqual(out.replace("\\", "/"), "D:/proj/render/shot_####", out)
        out2 = output_template_from("//outA\\frame_", "D:/proj/house.blend")
        self.assertEqual(out2.replace("\\", "/"), "D:/proj/outA/frame_####", out2)
        # `//` 后面带占位符/扩展名也照原样保留
        out3 = output_template_from("//r/f_####.png", "D:/proj/house.blend")
        self.assertEqual(out3.replace("\\", "/"), "D:/proj/r/f_####.png", out3)

    def test_double_slash_without_blend_drops(self):
        self.assertEqual(output_template_from("//render/shot_", ""), "")

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

    def test_multi_scene_is_mentioned(self):
        s = summarize({"ok": True, "blend": "D:/a/shot.blend", "scene": "SceneB",
                       "scenes": ["SceneA", "SceneB"], "engine": "CYCLES",
                       "resolution": [1920, 1080], "resolution_percentage": 100,
                       "samples": 256, "frame_start": 1, "frame_end": 240,
                       "frame_step": 1, "output_path": "D:/a/out_"})
        self.assertIn("共 2 个场景", s)
        self.assertIn("SceneB", s)

    def test_none_samples_is_printed_as_dash(self):
        """Workbench 这类没有采样数的引擎，别把 Python 的 None 打到界面上。"""
        s = summarize({"ok": True, "blend": "D:/a/s.blend", "scene": "Scene",
                       "engine": "BLENDER_WORKBENCH", "resolution": [10, 10],
                       "resolution_percentage": 100, "samples": None,
                       "frame_start": 1, "frame_end": 1, "frame_step": 1,
                       "output_path": ""})
        self.assertIn("采样 -", s)
        self.assertNotIn("None", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
