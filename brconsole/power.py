# -*- coding: utf-8 -*-
"""Windows 电源管理 —— 防睡眠 + 任务结束后关机/睡眠。

为什么要有这个模块：几千帧的过夜渲染，Windows 会按电源计划的空闲计时器把机器睡掉 ——
断点能续，但"早上起来只渲了一小时"的体验不可接受。微软官方机制是
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`：
告知系统"有应用在忙"，系统空闲计时器就不再触发睡眠。

⚠️ 官方文档的警告必须遵守：
- **别无限期持有**：任务结束（含取消/崩溃路径）必须清除，否则现代待机设备合盖也狂掉电。
  本模块的调用方（core.RenderJob）用 try/finally 保证成对。
- 这个 API 挡不住用户主动睡眠（合盖/按电源键），也不阻止屏保 —— 那是尊重用户的意思。

关机/睡眠动作设计成**可替换后端**（与 autostart.RegistryBackend 同款思路）：
单测绝不真关机，动作只到"拼出什么命令"这一层为止，由调用方执行。
"""

import os
import subprocess

# SetThreadExecutionState 的标志位（winbase.h）
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

SHUTDOWN_COUNTDOWN_SECONDS = 60      # 关机倒计时：给用户留取消的窗口


def supported():
    return os.name == "nt"


# ---------------- 防睡眠 ----------------
def keep_awake():
    """宣告"系统忙"，阻止空闲睡眠。幂等，可重复调用。

    返回值不可靠：API 成功时返回**上一次**的执行状态（可能是 0 = 之前没设过），
    失败返回 NULL —— 两者都是 0，分不清。而它实际失败的情形只有"标志非法"这一种，
    这里传的是写死的合法标志，所以直接按成功处理（真要较真得连调两次比对，不值得）。
    """
    if not supported():
        return False
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    return True


def allow_sleep():
    """清除防睡眠标志，把决定权还给电源计划。幂等（返回值语义同 keep_awake）。"""
    if not supported():
        return False
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    return True


# ---------------- 任务结束后的动作 ----------------
class CommandBackend(object):
    """真实后端：只负责把动作变成命令行并执行。单测注入假后端替代。"""

    def shutdown(self, seconds):
        """`shutdown /s /t <秒>`：自带倒计时，`shutdown /a` 可取消。"""
        return subprocess.run(["shutdown", "/s", "/t", str(int(seconds))],
                              capture_output=True, timeout=15)

    def sleep(self):
        """立即睡眠（可被断点续跑接住：唤醒后重新开机续渲不必，睡眠不断电）。"""
        # rundll32 不能用在自动化里（官方已弃用该调用方式）；SetSuspendState 才是正路
        import ctypes
        # POWR_ACTION = 0（Sleep），禁用唤醒不可（False），禁用休眠事件不强制（False）
        return ctypes.windll.PowrProf.SetSuspendState(False, False, False)

    def abort_shutdown(self):
        """取消 `shutdown /t` 的倒计时。"""
        return subprocess.run(["shutdown", "/a"],
                              capture_output=True, timeout=15)


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = CommandBackend()
    return _backend


def set_backend(b):
    """换后端（单测用）。传 None 恢复真实后端。"""
    global _backend
    _backend = b


def shutdown_after(seconds=SHUTDOWN_COUNTDOWN_SECONDS):
    """排一个带倒计时的关机。返回 (ok, message)。

    倒计时不是装饰：竞品统一给 60 秒取消窗口 —— 万一任务"假完成"（比如其实
    还有帧没渲），用户还有机会 `shutdown /a` 或点取消救回来。
    """
    try:
        r = backend().shutdown(seconds)
    except Exception as e:                      # noqa: BLE001 —— 动作失败不该崩界面
        return False, "关机命令执行失败：%s: %s" % (type(e).__name__, e)
    if getattr(r, "returncode", 1) != 0:
        detail = (getattr(r, "stderr", b"") or b"").decode("utf-8", "replace").strip()
        return False, "关机命令失败（rc=%s）%s" % (getattr(r, "returncode", "?"),
                                                  ("：" + detail) if detail else "")
    return True, "已排定 %d 秒后关机（可点「取消」或运行 shutdown /a 撤销）" % int(seconds)


def sleep_now():
    """立即睡眠。返回 (ok, message)。"""
    try:
        r = backend().sleep()
    except Exception as e:                      # noqa: BLE001
        return False, "睡眠命令执行失败：%s: %s" % (type(e).__name__, e)
    # CommandBackend.sleep 返回 ctypes 结果（非 0 = 成功）；注入后端可能返回 None
    ok = True if r is None else bool(r)
    return ok, "已进入睡眠" if ok else "系统拒绝了睡眠请求"


def abort_shutdown():
    """取消关机倒计时。返回 (ok, message)。"""
    try:
        r = backend().abort_shutdown()
    except Exception as e:                      # noqa: BLE001
        return False, "取消关机失败：%s: %s" % (type(e).__name__, e)
    if getattr(r, "returncode", 0) != 0:
        # rc=1116（没有可取消的关机）也走这里 —— 无害，如实报
        return False, "没有正在进行的关机倒计时（或取消失败，rc=%s）" % getattr(r, "returncode", "?")
    return True, "已取消关机倒计时"
