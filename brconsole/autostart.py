# -*- coding: utf-8 -*-
"""开机自启：往 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` 写一条。

为什么选注册表 Run 项而不是别的：

| 方案 | 免管理员 | 能改就能撤 | 备注 |
|---|---|---|---|
| **HKCU 的 Run 项** | ✅ | ✅ | 本方案。写自己的用户项不需要提权 |
| 启动文件夹放快捷方式 | ✅ | ✅ | 要造 .lnk，得靠 COM（pywin32）或 PowerShell，依赖更重 |
| 任务计划程序 | ❌ 多数选项要提权 | 一般 | 唯一能做「开机即启、无需登录」，但那时没有桌面会话，GUI 起不来 |

**只写 HKCU、绝不碰 HKLM**：HKLM 要提权，而且会影响别的用户。

注册表读写走可替换的 `RegistryBackend`，这样单测能在**不碰真实注册表**的前提下
测完整逻辑（往真注册表写测试项是不可接受的副作用）。
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "blender-render-console"
AUTOSTART_FLAG = "--autostart"

# Microsoft KB 179365 原文：「The data value for a key is a command line no longer
# than 260 characters.」超了会**写进去但开机不执行**（"看起来登记成功"是最坏的情况），
# 所以宁可不写、并当场告诉用户，也别留一条自启项在那骗人。
MAX_COMMAND_CHARS = 260


class RegistryBackend(object):
    """真实注册表后端。只做"取值 / 写值 / 删值"三件事。"""

    def get(self, name):
        if os.name != "nt":
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def set(self, name, value):
        if os.name != "nt":
            raise RuntimeError("开机自启只支持 Windows")
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, name):
        if os.name != "nt":
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
                return True
        except OSError:
            return False


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = RegistryBackend()
    return _backend


def set_backend(b):
    """换后端（单测用）。传 None 恢复真实注册表。"""
    global _backend
    _backend = b


def supported():
    return os.name == "nt"


# ---------------- 命令构造 ----------------
def project_root():
    """仓库根目录（brconsole 的上一层）—— 源码运行时 main.py 在这儿。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pythonw():
    """源码运行时优先用 pythonw.exe：Run 项没法指定「不弹控制台」，
    用 python.exe 会在登录后闪一个黑窗。没有 pythonw 就退回 python.exe。"""
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def launch_command():
    """写进注册表的那条命令。

    打包版：exe 自己就是入口 → `"<exe>" --autostart`
    源码版：需要显式带上 main.py → `"<pythonw>" "<main.py>" --autostart`
    """
    if getattr(sys, "frozen", False):
        return '"%s" %s' % (sys.executable, AUTOSTART_FLAG)
    return '"%s" "%s" %s' % (pythonw(), os.path.join(project_root(), "main.py"),
                             AUTOSTART_FLAG)


# ---------------- 开关 ----------------
def registered_command():
    """当前登记的命令；没登记返回 None。"""
    v = backend().get(VALUE_NAME)
    if not v or not str(v).strip():
        return None
    return str(v)


def is_enabled():
    return registered_command() is not None


def is_current():
    """登记的就是"现在这个 exe/脚本"吗。

    打包版换了目录、或源码版换了解释器，登记项就会指向旧位置 ——
    这时开机拉起来的是**另一个**程序，界面上必须提示重登记。
    """
    cur = registered_command()
    return bool(cur) and cur.strip() == launch_command().strip()


def enable():
    """开启自启，返回写进去的命令。命令过长时抛 `ValueError`（消息可直接给用户看）。"""
    cmd = launch_command()
    if len(cmd) > MAX_COMMAND_CHARS:
        raise ValueError(
            "路径太长：登记命令 %d 个字符，超过 Windows 允许的 %d 个，"
            "开机不会执行。请把程序挪到更短的目录（例如 D:\\Tool\\）再试。\n%s"
            % (len(cmd), MAX_COMMAND_CHARS, cmd))
    backend().set(VALUE_NAME, cmd)
    return cmd


def disable():
    return backend().delete(VALUE_NAME)


def sync(enabled):
    """把注册表状态调成 `enabled`。返回 (ok, message)，message 直接给用户看。"""
    if enabled and not supported():
        return False, "当前系统不支持开机自启（只做了 Windows）"
    try:
        if enabled:
            cmd = enable()
            return True, "已登记开机自启：%s" % cmd
        if not is_enabled():
            return True, "开机自启本来就是关的"
        disable()
        return True, "已取消开机自启"
    except ValueError as e:
        return False, str(e)                    # 我们自己抛的，消息已经是给人看的
    except Exception as e:
        return False, "改注册表失败：%s: %s" % (type(e).__name__, e)
