# -*- coding: utf-8 -*-
"""driver.py 的**契约测试**。

`driver.py` 跑在 Blender 进程内，本地没法端到端跑（要靠 `tools/smoke_real_blender.py` 真机验证）。
但它有两个部分**不碰 bpy 也能测**，而这两部分恰恰是"假 Blender 与真实现会分叉"的地方：

1. **扩展名表**：`tests/fake_blender.py`（假 Blender）与 `brconsole/driver.py`（真实现）
   各有一份。原来假的那份只有 PNG/JPEG/EXR **三项**，真的那份有 **13 项** —— 也就是说
   用假 Blender 跑的一切测试，都验不到 TIFF/BMP/WEBP 这些格式的输出文件名。
   这里把两张表**逐项锁死**，谁改了一边没改另一边就当场变红。
2. **落盘路径规则** `output_path_for()`：只要模板自带扩展名、或 job 里给了 `file_format`，
   它走的就是纯字符串分支（`file_format` 认不出来时才 `import bpy` 兜底），所以可以直接调。
   `AGENTS.md` 里记着两条实测结论（`write_still=True` 不替换 `####`；不能用 `frame_path()`
   算期望路径），这两条都该由用例钉住，而不是只写在注释里。
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from brconsole import driver                                        # noqa: E402
import fake_blender                                                 # noqa: E402


class TestExtensionTableIsShared(unittest.TestCase):
    """假 Blender 与真 driver 的扩展名表必须逐项相等。"""

    def test_tables_are_identical(self):
        self.assertEqual(
            fake_blender.FORMAT_EXT, driver.FORMAT_EXT,
            "两张表分叉了：假 Blender 要跟 brconsole/driver.py 的 FORMAT_EXT 保持一致，"
            "否则用假 Blender 跑的测试全部验不到真机的落盘文件名")

    def test_fake_covers_every_format_the_gui_offers(self):
        """界面下拉里能选的格式，假 Blender 都得认识（不然测试会漏掉它们）。"""
        from brconsole.guimodel import FORMATS
        for _label, value in FORMATS:
            if not value:                      # 「保持工程设置」的值就是空串
                continue
            self.assertIn(value, fake_blender.FORMAT_EXT, value)


class TestOutputPathRules(unittest.TestCase):
    """`output_path_for` 的落盘路径规则（纯字符串分支，不需要 bpy）。"""

    def setUp(self):
        self._old = driver.JOB

    def tearDown(self):
        driver.JOB = self._old

    def path_for(self, template, fmt, frame=1):
        driver.JOB = {"output_template": template, "file_format": fmt}
        return driver.output_path_for(frame)

    def test_hash_placeholder_is_replaced_by_hand(self):
        """`####` 必须自己替换 —— Blender 的 `write_still=True` 不替换它（实测）。"""
        self.assertEqual(self.path_for("out/f_####", "PNG"), "out/f_0001.png")
        self.assertEqual(self.path_for("out/f_####", "PNG", 42), "out/f_0042.png")

    def test_extension_comes_from_the_format(self):
        for fmt, ext in (("PNG", "png"), ("TIFF", "tif"), ("OPEN_EXR", "exr"),
                         ("JPEG2000", "jp2"), ("WEBP", "webp")):
            got = self.path_for("out/f_####", fmt)
            self.assertTrue(got.endswith("f_0001." + ext), (fmt, got))

    def test_template_carrying_its_own_extension_is_not_doubled(self):
        """模板自带扩展名时不能再追加一次（否则会得到 `f_0001.jpeg.png`）。"""
        self.assertEqual(self.path_for("out/f_####.jpeg", "PNG"), "out/f_0001.jpeg")

    def test_dot_in_directory_does_not_count_as_an_extension(self):
        """目录名里带点是常事，别把它当成"模板已带扩展名"。"""
        self.assertEqual(self.path_for("out.v2/f_####", "PNG"), "out.v2/f_0001.png")

    def test_unknown_format_falls_back(self):
        """认不出的格式要能兜住（这条会走到 import bpy 的 except 分支）。"""
        got = self.path_for("out/f_####", "NOT_A_FORMAT")
        self.assertTrue(got.startswith("out/f_0001."), got)


if __name__ == "__main__":
    unittest.main()
