# -*- coding: utf-8 -*-
"""进程级单实例锁 —— Windows 命名互斥量。

背景：渲染是独占 GPU 的重活，而且两个实例会**互踩共享文件** ——
`.render_state.json`（断点）与输出图（同名的 `f_0001.png` 互相覆盖）。
Blender 官方也踩过同一类坑：两个实例渲同一工程时临时 EXR 互相覆盖导致损坏
（blender#37974，官方最后给临时文件名加盐 PID 才修掉）。所以界面实例必须唯一。

为什么用**命名互斥量**而不是"锁文件 + PID"（检索结论，2026-10-05，微软推荐前者）：

| | 命名互斥量 | 锁文件 + PID |
|---|---|---|
| 崩溃/掉电 | **内核自动回收**，无残锁 | 残锁把用户挡在门外（VMware 的 .vmdk 锁是著名案例） |
| 创建 | 原子（要么新建要么发现已存在） | "检查再创建"有竞态窗口 |
| PID 复用 | 无此问题 | 残留 PID 被新进程复用时误判 |

作用域用 `Local\\` 前缀（**按登录会话隔离**）：渲染是桌面 GUI 活，快速用户切换 / RDP
下各会话各跑各的反而合理 —— 这也是微软对普通桌面应用的建议（跨会话才用 `Global\\`）。

只锁**界面实例**：CLI（`brc.exe`）不锁 —— 脚本调用是有意的并发（用户自己排的队），
拦它只会碍事；输出目录冲突那种风险由用户在脚本里自己错开。
"""

import os

MUTEX_NAME = "Local\\blender-render-console-single-instance"

ERROR_ALREADY_EXISTS = 183


def supported():
    return os.name == "nt"


def acquire(name=MUTEX_NAME):
    """尝试成为唯一实例。返回 `(ok, handle)`。

    `handle` 必须**一直攥着**（进程活着锁就在；正常退出交给 `release()`，
    崩溃由内核回收）。互斥量创建失败（权限等极少见情况）时**放行** ——
    把用户挡在门外，比罕见的一次双开更糟。
    """
    if not supported():
        return True, None
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True, None                      # 创建失败：宁可放行
    if ctypes.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False, None
    return True, handle


def release(handle):
    """交出单实例锁。崩溃场景不需要它 —— 内核会自己回收。"""
    if handle:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(handle)


def focus_window(title):
    """把已运行的实例窗口调到前台（最小化先还原）。找到并成功返回 True。"""
    if not supported():
        return False
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True
