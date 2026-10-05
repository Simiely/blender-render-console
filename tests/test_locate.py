# -*- coding: utf-8 -*-
"""blender.exe 探测的测试。

`locate.py` 原先**一条单测都没有** —— 它是"文件系统扫描"，用临时目录完全可测，
不属于"没法单测所以只能靠真机"那一类（那类是 `driver.py`，跑在 Blender 进程内）。

⚠️ 每个用例都把 `PATH` 清空、把 `BRC_BLENDER` / `BLENDER_EXE` 摘掉、并且永远显式传
`roots=`：否则 `find_blender` 会去扫**开发机真实**的 `C:\\Program Files\\Blender Foundation`，
测试就跟着那台机器跑了。
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from brconsole import locate                                        # noqa: E402

WATCHED_ENV = ("PATH", "BRC_BLENDER", "BLENDER_EXE", "PROGRAMFILES", "LOCALAPPDATA")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=".brc-locate-")
        self._env = {k: os.environ.get(k) for k in WATCHED_ENV}
        os.environ["PATH"] = ""
        for k in ("BRC_BLENDER", "BLENDER_EXE"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)

    def exe(self, *parts):
        """造一个假的 blender.exe（locate 只检查存在性）。"""
        p = os.path.join(self.dir, *parts)
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(p, "wb") as f:
            f.write(b"MZ")
        return p


class TestVersionFromPath(Base):
    def test_official_layout(self):
        p = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
        self.assertEqual(locate._version_from_path(p), (5, 2, 0))

    def test_patch_version(self):
        self.assertEqual(locate._version_from_path(r"D:\Blender 4.1.3\blender.exe"),
                         (4, 1, 3))

    def test_two_versions_takes_the_higher(self):
        """路径里出现两段版本号时取高的 —— 便携版目录常套一层。"""
        self.assertEqual(locate._version_from_path(r"D:\Blender 3.6\Blender 5.2\blender.exe"),
                         (5, 2, 0))

    def test_no_version(self):
        self.assertEqual(locate._version_from_path(r"D:\tools\blender.exe"), (0, 0, 0))

    def test_ignores_non_version_numbers_in_the_file_name(self):
        """`blender.exe` 本身没有版本段，别把它当版本号。"""
        self.assertEqual(locate._version_from_path(r"D:\x\blender.exe"), (0, 0, 0))


class TestScanRoot(Base):
    def test_finds_the_exe(self):
        want = self.exe("Blender 5.2", "blender.exe")
        self.assertEqual(locate.scan_root(self.dir), [want])

    def test_depth_limit(self):
        self.exe("a", "b", "c", "blender.exe")
        self.assertEqual(locate.scan_root(self.dir, max_depth=2), [])
        self.assertEqual(len(locate.scan_root(self.dir, max_depth=3)), 1)

    def test_skips_noise_dirs(self):
        """`node_modules` / `.git` 这类目录不该扫 —— 大仓库里能拖住好几秒。"""
        self.exe("node_modules", "blender.exe")
        self.exe(".git", "blender.exe")
        self.assertEqual(locate.scan_root(self.dir), [])

    def test_missing_root_is_not_an_error(self):
        self.assertEqual(locate.scan_root(os.path.join(self.dir, "nope")), [])
        self.assertEqual(locate.scan_root(""), [])
        self.assertEqual(locate.scan_root(None), [])

    def test_respects_limit(self):
        for i in range(5):
            self.exe("d%d" % i, "blender.exe")
        self.assertLessEqual(len(locate.scan_root(self.dir, limit=2)), 2)

    def test_deadline_stops_the_walk(self):
        self.exe("Blender 5.2", "blender.exe")
        self.assertEqual(locate.scan_root(self.dir, deadline=0), [])

    def test_matches_case_insensitively(self):
        """Windows 上文件名大小写不敏感，`Blender.EXE` 也得认。"""
        self.exe("Blender 5.2", "Blender.EXE")
        self.assertEqual(len(locate.scan_root(self.dir)), 1)


class TestFindBlender(Base):
    def test_env_var_wins_over_scanning(self):
        want = self.exe("manual", "blender.exe")
        os.environ["BRC_BLENDER"] = want
        self.assertEqual(locate.find_blender(roots=[]), [want])

    def test_both_env_vars_are_kept_brc_first(self):
        a = self.exe("a", "blender.exe")
        b = self.exe("b", "blender.exe")
        os.environ["BRC_BLENDER"] = a
        os.environ["BLENDER_EXE"] = b
        self.assertEqual(locate.find_blender(roots=[]), [a, b])

    def test_env_var_pointing_nowhere_is_skipped(self):
        """环境变量写错了不该让整条探测链崩掉，也不该把不存在的路径塞进候选。"""
        os.environ["BRC_BLENDER"] = os.path.join(self.dir, "nope", "blender.exe")
        self.assertEqual(locate.find_blender(roots=[]), [])

    def test_newer_version_sorts_first(self):
        old = self.exe("Blender 4.1", "blender.exe")
        new = self.exe("Blender 5.2", "blender.exe")
        got = locate.find_blender(roots=[self.dir])
        self.assertEqual(sorted(got), sorted([old, new]))
        self.assertEqual(got[0], new)                # 新版优先

    def test_duplicates_are_collapsed(self):
        a = self.exe("Blender 5.2", "blender.exe")
        os.environ["BRC_BLENDER"] = a
        self.assertEqual(locate.find_blender(roots=[self.dir]), [a])

    def test_roots_are_scanned_in_order(self):
        self.exe("only", "blender.exe")
        self.assertEqual(len(locate.find_blender(roots=[self.dir, self.dir])), 1)


class TestResolveBlender(Base):
    def test_explicit_path_is_returned_as_is(self):
        want = self.exe("blender.exe")
        self.assertEqual(locate.resolve_blender(explicit=want),
                         (os.path.abspath(want), [os.path.abspath(want)]))

    def test_explicit_missing_path_is_fatal(self):
        """显式指定的路径不存在 → 直接退出并说清楚，别退回去猜。"""
        missing = os.path.join(self.dir, "nope", "blender.exe")
        with self.assertRaises(SystemExit) as cm:
            locate.resolve_blender(explicit=missing)
        self.assertIn(missing, str(cm.exception))

    def test_auto_detect_shape(self):
        """自动探测这条**不写死结果**（它要扫真实的 Program Files）。

        只断言"形状"：要么 (None, [])，要么首元素就是返回的那个路径且真的存在。
        """
        path, cands = locate.resolve_blender()
        if path is None:
            self.assertEqual(cands, [])
        else:
            self.assertEqual(path, cands[0])
            self.assertTrue(os.path.exists(path), path)


if __name__ == "__main__":
    unittest.main()
