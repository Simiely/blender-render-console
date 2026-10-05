# -*- coding: utf-8 -*-
"""磁盘空间预检的测试。

**不依赖真实磁盘余量**：所有判定都通过 `free_fn` 注入固定字节数 ——
拿机器的真实剩余空间当断言条件，就是一条"换台机器就红"的假测试。
真实探测那一层另有 `test_real_probe_reads_a_number`，只验"调用约定没接错"。
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from brconsole import diskspace                                    # noqa: E402
from brconsole.core import JobConfig                               # noqa: E402

GIB = diskspace.GIB
OTHER_OUT = "Z:\\out\\frame_####"


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def missing_drive():
    """找一个**当前不存在**的盘符（用来演"别的盘"）。"""
    for ch in "ZYXWVUTSRQPONMLKJIHG":
        if not os.path.exists(ch + ":\\"):
            return ch + ":\\"
    return None


class Base(unittest.TestCase):
    def setUp(self):
        self._old_sys = os.environ.get("SystemDrive")
        os.environ["SystemDrive"] = "C:"           # 固定下来，别让断言跟着机器跑
        self.tmp = tempfile.mkdtemp(prefix="brc-disk-")
        self.blend = os.path.join(self.tmp, "house.blend")
        with open(self.blend, "wb") as f:
            f.write(b"BLENDER-v5")
        self.out = os.path.join(self.tmp, "out", "frame_####")

    def tearDown(self):
        if self._old_sys is None:
            os.environ.pop("SystemDrive", None)
        else:
            os.environ["SystemDrive"] = self._old_sys
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, out=None, **kw):
        args = dict(blend=self.blend, frames=[1, 2, 3, 4],
                    output_template=out or self.out,
                    resolution=(1920, 1080), file_format="PNG")
        args.update(kw)
        return JobConfig(**args)

    @staticmethod
    def free(n):
        return lambda folder: n


class TestDriveKey(Base):
    def test_normalises(self):
        self.assertEqual(diskspace.drive_key("C:\\a\\b"), "C:\\")
        self.assertEqual(diskspace.drive_key("c:/a/b"), "C:\\")
        self.assertEqual(diskspace.drive_key("Z:\\"), "Z:\\")

    def test_unc_keeps_the_share(self):
        # UNC 的 splitdrive 已经给到共享名，再补盘符就是错的
        self.assertEqual(diskspace.drive_key("\\\\srv\\share\\a"), "\\\\SRV\\SHARE")

    def test_system_drive(self):
        self.assertTrue(diskspace.is_system_drive("C:\\x"))
        self.assertTrue(diskspace.is_system_drive("c:/x"))
        self.assertFalse(diskspace.is_system_drive("Z:\\x"))

    def test_follows_the_env(self):
        """跟着 SystemDrive 走，而不是把 C: 写死。"""
        os.environ["SystemDrive"] = "Q:"
        self.assertTrue(diskspace.is_system_drive("Q:\\x"))
        self.assertFalse(diskspace.is_system_drive("C:\\x"))


class TestNearestExistingDir(Base):
    def test_existing_dir_returns_itself(self):
        self.assertEqual(norm(diskspace.nearest_existing_dir(self.tmp)), norm(self.tmp))

    def test_walks_up_to_a_real_dir(self):
        """输出目录常常是用户刚填、还没建的 —— 必须能降级到最近的已存在祖先。"""
        deep = os.path.join(self.tmp, "a", "b", "c")
        self.assertFalse(os.path.isdir(deep))
        self.assertEqual(norm(diskspace.nearest_existing_dir(deep)), norm(self.tmp))

    def test_missing_root_returns_none(self):
        root = missing_drive()
        if root is None:
            self.skipTest("找不到未占用的盘符")
        self.assertIsNone(diskspace.nearest_existing_dir(root + "nope\\deep"))


class TestFreeBytes(Base):
    def test_injected_value_passes_through(self):
        self.assertEqual(diskspace.free_bytes(self.out, free_fn=self.free(123)), 123)

    def test_none_means_unknown(self):
        self.assertIsNone(diskspace.free_bytes(self.out, free_fn=self.free(None)))

    def test_exception_is_swallowed(self):
        """探测炸了不能拖垮开工 —— 当成"不知道"。"""
        def boom(_folder):
            raise OSError("设备未就绪")
        self.assertIsNone(diskspace.free_bytes(self.out, free_fn=boom))

    def test_negative_is_clamped(self):
        self.assertEqual(diskspace.free_bytes(self.out, free_fn=self.free(-5)), 0)

    def test_real_probe_reads_a_number(self):
        """真实探测那层：约定是"先向上找已存在的目录、再问系统"，这里只验它接得通。"""
        got = diskspace.free_bytes(self.out)       # out 本身还不存在
        self.assertIsNotNone(got)
        self.assertGreater(got, 0)


class TestEstimate(Base):
    def test_pixels_with_percentage(self):
        self.assertEqual(diskspace.frame_pixels((1920, 1080)), 1920 * 1080)
        self.assertEqual(diskspace.frame_pixels((1920, 1080), 50), 960 * 540)
        self.assertEqual(diskspace.frame_pixels((1920, 1080), 100), 1920 * 1080)

    def test_pixels_bad_input(self):
        for bad in (None, (), (0, 100), (-1, 10), ("x", "y")):
            self.assertIsNone(diskspace.frame_pixels(bad), bad)

    def test_png_uses_four_bytes_per_pixel(self):
        self.assertEqual(diskspace.estimate_bytes([1, 2, 3], (10, 10), None, "PNG"),
                         3 * 100 * 4)

    def test_exr_uses_eight_bytes_per_pixel(self):
        self.assertEqual(diskspace.estimate_bytes([1, 2], (10, 10), None, "OPEN_EXR"),
                         2 * 100 * 8)

    def test_unknown_format_uses_the_biggest(self):
        """「保持工程设置」也算不出格式 —— 按最大的估，宁可偏早提醒。"""
        self.assertEqual(diskspace.estimate_bytes([1], (10, 10), None, None),
                         100 * diskspace.DEFAULT_BYTES_PER_PIXEL)

    def test_needs_both_frames_and_resolution(self):
        self.assertIsNone(diskspace.estimate_bytes([], (10, 10), None, "PNG"))
        self.assertIsNone(diskspace.estimate_bytes([1, 2], None, None, "PNG"))

    def test_human(self):
        self.assertEqual(diskspace.human(None), "未知")
        self.assertEqual(diskspace.human(3 * GIB), "3.0 GB")
        self.assertEqual(diskspace.human(5 * diskspace.MIB), "5 MB")


class TestCheck(Base):
    def test_roomy_is_ok(self):
        self.assertEqual(diskspace.check(self.cfg(), free_fn=self.free(500 * GIB)),
                         ("ok", ""))

    def test_unknown_space_never_blocks(self):
        """拿不到空间信息就不拦 —— 拦不住的代价远小于误拦。"""
        self.assertEqual(diskspace.check(self.cfg(), free_fn=self.free(None)), ("ok", ""))

        def boom(_folder):
            raise OSError("boom")
        self.assertEqual(diskspace.check(self.cfg(), free_fn=boom), ("ok", ""))

    def test_no_output_path_is_ok(self):
        """构造中的表单读不到输出路径 —— 不该拦。"""
        class Dummy(object):
            output_template = ""
            frames = [1]
        self.assertEqual(diskspace.check(Dummy()), ("ok", ""))

    def test_system_drive_blocks_below_the_floor(self):
        level, why = diskspace.check(self.cfg(), free_fn=self.free(5 * GIB))
        self.assertEqual(level, "block")
        self.assertIn("系统盘", why)
        self.assertIn("5.0 GB", why)

    def test_other_drive_tolerates_the_same_5gib(self):
        """同样 5 GB：别的盘上够用（只是不宽裕），系统盘上不行。"""
        level, why = diskspace.check(self.cfg(out=OTHER_OUT), free_fn=self.free(5 * GIB))
        self.assertEqual(level, "ok", why)

    def test_other_drive_blocks_below_its_floor(self):
        level, why = diskspace.check(self.cfg(out=OTHER_OUT), free_fn=self.free(1 * GIB))
        self.assertEqual(level, "block")
        self.assertIn("其它盘", why)

    def test_system_floor_is_strictly_higher(self):
        """两个阈值必须真的不同 —— 写成一样就等于"系统盘没有额外保护"。"""
        self.assertGreater(diskspace.MIN_FREE_SYSTEM, diskspace.MIN_FREE_OTHER)

    def test_message_names_the_folder(self):
        """消息要能照着去改，所以必须带上那个目录。"""
        level, why = diskspace.check(self.cfg(), free_fn=self.free(1 * GIB))
        self.assertEqual(level, "block")
        self.assertIn("out", why)

    def test_warns_when_output_may_not_fit(self):
        """过了水位线，但"最多可能写到"超过可用空间 → 提醒，不拦。"""
        c = self.cfg(frames=list(range(1, 2001)))          # 上界 ≈ 15.5 GiB
        need = diskspace.estimate_bytes(c.frames, c.resolution, None, "PNG")
        self.assertGreater(need, 12 * GIB)                 # 前提：这确实是个大任务
        level, why = diskspace.check(c, free_fn=self.free(12 * GIB))
        self.assertEqual(level, "warn")
        self.assertIn("最多可能写到", why)

    def test_fitting_task_does_not_warn(self):
        """小任务在同样空间下不该有噪音。"""
        level, _ = diskspace.check(self.cfg(), free_fn=self.free(12 * GIB))
        self.assertEqual(level, "ok")

    def test_block_beats_warn(self):
        """两档同时成立时必须报 block —— 否则"再写一点就满"的任务会被放行。"""
        c = self.cfg(frames=list(range(1, 2001)))
        self.assertEqual(diskspace.check(c, free_fn=self.free(1 * GIB))[0], "block")


if __name__ == "__main__":
    unittest.main()
