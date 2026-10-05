# -*- coding: utf-8 -*-
"""单实例锁测试。要害：互斥语义真的成立 + 崩溃（不 release）也不会把人挡在门外。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brconsole import applock  # noqa: E402

# 用**独立的名字**：真程序在用户机器上跑着时，测试也不能被它挡住（反之亦然）
TEST_NAME = "Local\\brc-test-single-instance-%d" % os.getpid()


@unittest.skipUnless(applock.supported(), "只实现了 Windows 命名互斥量")
class TestAppLock(unittest.TestCase):
    def test_second_acquire_is_denied(self):
        ok, handle = applock.acquire(TEST_NAME)
        try:
            self.assertTrue(ok)
            # 同进程第二次拿同一把锁，必须被拒（互斥语义；真场景是第二个进程）
            ok2, handle2 = applock.acquire(TEST_NAME)
            self.assertFalse(ok2)
            self.assertIsNone(handle2)
        finally:
            applock.release(handle)

    def test_release_allows_reacquire(self):
        ok, handle = applock.acquire(TEST_NAME)
        self.assertTrue(ok)
        applock.release(handle)
        ok2, handle2 = applock.acquire(TEST_NAME)
        try:
            self.assertTrue(ok2)        # 交还之后必须拿得到（否则残锁挡人）
        finally:
            applock.release(handle2)

    def test_crash_without_release_does_not_stick(self):
        """模拟"上一个实例崩了没 release"：内核回收互斥量，新实例必须进得来。

        这是选互斥量而不是锁文件的理由 —— 锁文件方案这里会留下残锁，
        用户得手动去删文件（VMware .vmdk 的著名案例）。
        """
        ok, handle = applock.acquire(TEST_NAME)
        self.assertTrue(ok)
        # 故意不 release，直接把句柄"忘掉"（函数返回后本地变量销毁；即便句柄
        # 因引用被进程持有，互斥量的语义也只看进程存活的句柄数 —— 用新名字验证不行，
        # 所以这里换成"关闭句柄"来近似进程退出）：
        import ctypes
        ctypes.windll.kernel32.CloseHandle(handle)
        ok2, handle2 = applock.acquire(TEST_NAME)
        try:
            self.assertTrue(ok2)
        finally:
            applock.release(handle2)

    def test_releasing_none_is_harmless(self):
        applock.release(None)           # 非 Windows / 未拿到锁时的路径，不该抛


if __name__ == "__main__":
    unittest.main()
