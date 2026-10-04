# -*- coding: utf-8 -*-
"""tkinter 自举：本机托管 Python **不带 tkinter**，需要 sidecar 才能起界面。

sidecar 由 `tools/build_tkinter.py` 生成（从 python.org 安装包里纯文件提取）。
它得靠三样东西生效（缺一个都 import 不到 `_tkinter`）：
    PYTHONPATH  += sidecar/Lib ; sidecar/DLLs
    TCL_LIBRARY  = sidecar/tcl/tcl8.6
    TK_LIBRARY   = sidecar/tcl/tk8.6
    PATH         = sidecar/DLLs 前缀

做法：发现 `import tkinter` 失败且 sidecar 存在时，**带好环境变量重启自己一次**
（用 `BRC_TK_BOOTSTRAP=1` 标记，防止无限重启）。用户只要 `python main.py` 就够了。
"""

import os
import subprocess
import sys

BOOT_FLAG = "BRC_TK_BOOTSTRAP"


def sidecar_env(root, base=None):
    """返回给子进程用的环境 dict；sidecar 不存在时返回 None。"""
    side = os.path.join(root, "sidecar")
    dlls = os.path.join(side, "DLLs")
    lib = os.path.join(side, "Lib")
    if not (os.path.isdir(dlls) and os.path.isdir(lib)):
        return None
    env = dict(os.environ if base is None else base)
    sep = os.pathsep
    env["PYTHONPATH"] = sep.join(
        p for p in (lib, dlls, env.get("PYTHONPATH", "")) if p)
    env["TCL_LIBRARY"] = os.path.join(side, "tcl", "tcl8.6")
    env["TK_LIBRARY"] = os.path.join(side, "tcl", "tk8.6")
    env["PATH"] = dlls + sep + env.get("PATH", "")
    env[BOOT_FLAG] = "1"
    return env


def has_tkinter():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


# ---------- 打包后（PyInstaller）----------
# exe 里 Python 同样不带 tkinter，sidecar 由打包脚本塞进 `<_MEIPASS>/_brc`，
# 这里是把它挂回运行环境的那条路。必须在 `import tkinter` 之前调用。
FROZEN_SUBDIR = "_brc"


def frozen_root():
    """打包后的资源根目录；源码运行时返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)


def apply_frozen_env(root=None):
    """把 exe 自带的 tcl/tk 与 tkinter 包挂上。返回是否生效。"""
    base = root or frozen_root()
    if not base:
        return False
    side = os.path.join(base, FROZEN_SUBDIR)
    dlls = os.path.join(side, "DLLs")
    lib = os.path.join(side, "Lib")

    ok = False
    if os.path.isdir(dlls):
        # Python 3.8+ 不再用 PATH 搜扩展模块的依赖 DLL，必须显式注册
        try:
            os.add_dll_directory(dlls)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = dlls + os.pathsep + os.environ.get("PATH", "")
        # DLLs 也要进 sys.path：`_tkinter.pyd` 是顶层扩展模块，光有 PATH 找不到它
        for p in (dlls, lib):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
        ok = True
    # 必须是**赋值而不是 setdefault**：有的机器上系统环境变量里已经有一个
    # 八竿子打不着的 TCL_LIBRARY（实测遇到过指向别的软件的 tcl 数据目录），
    # 一旦沿用就会 `version conflict for package "Tcl"`，而且 tkinter 自己的
    # 推导路径（`<前缀>/lib/tcl8.6`）跟我们的布局也对不上。
    for var, parts in (("TCL_LIBRARY", ("tcl", "tcl8.6")),
                       ("TK_LIBRARY", ("tcl", "tk8.6"))):
        d = os.path.join(side, *parts)
        if os.path.isdir(d):
            os.environ[var] = d
            ok = True
    return ok


def bootstrap(root, argv=None):
    """需要时带 sidecar 重启当前进程；返回 False 表示没法自举（调用方给出提示）。

    重启成功时本函数**不会返回**（子进程接管，父进程随后退出）。
    """
    if has_tkinter():
        return True
    if os.environ.get(BOOT_FLAG):
        return False                       # 已经重启过一次还是不行，别循环
    env = sidecar_env(root)
    if env is None:
        return False
    argv = list(sys.argv[1:] if argv is None else argv)
    script = os.path.abspath(sys.argv[0] or "main.py")
    rc = subprocess.run([sys.executable, script] + argv, env=env).returncode
    sys.exit(rc)
