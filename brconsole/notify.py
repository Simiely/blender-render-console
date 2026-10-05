# -*- coding: utf-8 -*-
"""任务完成通知 —— 三层递减，前两层零依赖必成功，第三层尽力而为。

过夜渲染人不在屏幕前，"渲完了/失败了"必须能穿过屏保与静音的注意力盲区送达：

1. **提示音** `MessageBeep` —— 系统级，永远有效
2. **任务栏闪烁** `FlashWindowEx` —— 窗口在任务栏上橙黄闪烁直到被点亮，
   用户切屏回来一眼就能看到
3. **Toast 通知**（Windows 10+）—— 走 PowerShell 的 WinRT 投影，**尽力而为**：
   系统策略/精简系统可能没有 WinRT 投影，失败一律静默（前两层已兜底）

全部走可替换后端（单测不真响不真弹）。
"""

import os
import subprocess

APP_ID = "blender-render-console"


def supported():
    return os.name == "nt"


# ---------------- 后端 ----------------
class WinBackend(object):
    """真实后端：ctypes 直调 user32 + PowerShell 投影 toast。"""

    def beep(self):
        import ctypes
        MB_ICONINFORMATION = 0x40
        return bool(ctypes.windll.user32.MessageBeep(MB_ICONINFORMATION))

    def flash(self, tk_winfo_id):
        """让 tk 窗口在任务栏上闪烁（FLASHW_ALL | FLASHW_TIMERNOFG）。"""
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(tk_winfo_id) or tk_winfo_id

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_void_p)]

        FLASHW_ALL = 0x3
        FLASHW_TIMERNOFG = 0xC
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                          FLASHW_ALL | FLASHW_TIMERNOFG, 5, None)
        return bool(user32.FlashWindowEx(ctypes.byref(info)))

    def toast(self, title, message):
        """PowerShell 投影 WinRT 弹 toast。成功/失败都可能，调用方不依赖它。"""
        script = (
            "[void][Windows.UI.Notifications.ToastNotificationManager,"
            " Windows.UI.Notifications, ContentType=WindowsRuntime];"
            "[void][Windows.Data.Xml.Dom.XmlDocument,"
            " Windows.Data.Xml.Dom, ContentType=WindowsRuntime];"
            "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$x=@'\n{title}\n{message}\n'@ -split \"`n\";"
            "$t.GetElementsByTagName('text').Item(0).AppendChild("
            "$t.CreateTextNode($x[0]))|Out-Null;"
            "$t.GetElementsByTagName('text').Item(1).AppendChild("
            "$t.CreateTextNode($x[1]))|Out-Null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'" + APP_ID + "').Show("
            "[Windows.UI.Notifications.ToastNotification]::new($t))"
        ).format(title=title.replace("'", " "), message=message.replace("'", " "))
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", script],
            capture_output=True, timeout=20)
        return r.returncode == 0


_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = WinBackend()
    return _backend


def set_backend(b):
    """换后端（单测用）。传 None 恢复真实后端。"""
    global _backend
    _backend = b


# ---------------- 对外 ----------------
def announce(tk_winfo_id=None, title="渲染任务", message="", toast=True):
    """发一条完成通知。返回 dict 说明每层是否送达（供日志展示，调用方别拿它做判断）。

    前两层（提示音 + 闪烁）任何一层成功就算送达；toast 失败静默。
    """
    report = {"beep": False, "flash": False, "toast": False}
    if not supported():
        return report
    try:
        report["beep"] = bool(backend().beep())
    except Exception:                           # noqa: BLE001 —— 通知永远不该把主流程搞崩
        pass
    if tk_winfo_id:
        try:
            report["flash"] = bool(backend().flash(tk_winfo_id))
        except Exception:                       # noqa: BLE001
            pass
    if toast:
        try:
            report["toast"] = bool(backend().toast(title, message))
        except Exception:                       # noqa: BLE001
            pass
    return report
