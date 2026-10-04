# -*- coding: utf-8 -*-
r"""打包成单个 exe（PyInstaller）。

    python tools/build_exe.py                # 打 GUI 版（--windowed，双击用界面）
    python tools/build_exe.py --console      # 打命令行版（保留控制台输出）
    python tools/build_exe.py --both         # 两个都打
    python tools/build_exe.py --keep-build   # 保留 build/ 便于排错

产出在 `dist/`：
    blender-render-console.exe   图形界面版（--windowed）
    brc.exe                      命令行版（--console，输出能打到终端）

图标与版本信息：
- `assets/app.ico` 由 `tools/make_icon.py` 生成（纯标准库，**不需要 Pillow**），
  打包时写进 PE 资源；**窗口**图标另走一份数据文件，运行时从 `_brc/assets/app.ico` 读
- 版本号唯一来源是 `brconsole/__init__.py` 的 `__version__` —— 不在这里另存一份，
  否则一定会漂（已经漂过一次：文档写到 0.3.0 而代码里还是 0.2.0）

⚠️ 前置条件（缺一个就打不出能用的界面）：
1. `sidecar/` 必须存在 —— 本机 Python **不带 tkinter**，界面靠它（`python tools/build_tkinter.py`）
2. sidecar 要**整棵 tcl 目录**一起进包（含 `dde1.4` / `reg1.3` / `tcl8`），只挑 tcl8.6 会挂
3. 用**装了 PyInstaller 的隔离 venv** 跑（脚本会自己找，找不到就报错，不会污染系统 Python）
4. 主程序里 `tkboot.apply_frozen_env()` 必须在 `import tkinter` 之前执行 ——
   exe 里的 Python 同样没有 tkinter，得从 `<_MEIPASS>/_brc` 把 tcl/tk 挂回去

为什么手动 --add-data 而不是靠 PyInstaller 的 tkinter hook：
宿主 Python 里根本没有 tkinter，Analysis 阶段找不到这个模块，hook 不会运行；
即便强行用 --paths 指过去，它的 tcl/tk 数据目录也不在标准位置（我们的是 sidecar/tcl/*）。
所以索性自己把 DLLs / Lib/tkinter / tcl 三个目录塞进 `_brc`，运行时由 `tkboot` 挂上。
"""

import argparse
import ctypes
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIDECAR = os.path.join(ROOT, "sidecar")
ENTRY = os.path.join(ROOT, "main.py")
ICON = os.path.join(ROOT, "assets", "app.ico")

# 隔离 venv：PyInstaller 只装在里面（不污染用户环境）
VENV_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "python",
                 "envs", "default", "Scripts", "python.exe"),
]

GUI_NAME = "blender-render-console"
CLI_NAME = "brc"
FROZEN_SUBDIR = "_brc"          # 与 tkboot.FROZEN_SUBDIR 保持一致


def log(msg):
    print(msg, flush=True)


def find_python():
    for p in VENV_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def check_sidecar():
    need = ["DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll",
            "Lib/tkinter/__init__.py", "tcl/tcl8.6/init.tcl", "tcl/tk8.6/tk.tcl",
            "tcl/dde1.4/pkgIndex.tcl", "tcl/dde1.4/tcldde14.dll",
            "tcl/reg1.3/pkgIndex.tcl", "tcl/reg1.3/tclreg13.dll"]
    missing = [n for n in need if not os.path.exists(os.path.join(SIDECAR, n))]
    if missing:
        raise SystemExit("sidecar 不完整，缺：%s\n先跑：python tools/build_tkinter.py"
                         % ", ".join(missing))


# ---------------------------------------------------------------- 图标与版本信息
def ensure_icon():
    """没有 assets/app.ico 就现生成。

    `tools/make_icon.py` 是纯标准库的，直接调它的函数，不必起子进程
    （也因此不需要 Pillow —— 打包环境里只有 PyInstaller）。
    """
    if os.path.exists(ICON):
        return True
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import make_icon
    data, used = make_icon.build_ico()
    os.makedirs(os.path.dirname(ICON), exist_ok=True)
    with open(ICON, "wb") as f:
        f.write(data)
    log("  ✓ 现生成图标 %s（%s px）" % (ICON, ",".join(str(s) for s in used)))
    return True


def read_version():
    """版本号**只认 `brconsole/__init__.py`**。

    写死两份就一定会漂 —— 之前 `__version__` 停在 0.2.0 而文档已经写到 0.3.0，
    就是因为改文档时没人记得回来改代码。所以打包时现读，让唯一来源说了算。
    """
    src = io.open(os.path.join(ROOT, "brconsole", "__init__.py"), encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', src)
    if not m:
        raise SystemExit("brconsole/__init__.py 里找不到 __version__")
    text = m.group(1)
    parts = []
    for x in text.split("."):
        parts.append(int(x) if x.isdigit() else 0)
    parts = (parts + [0, 0, 0, 0])[:4]
    return text, tuple(parts)


VERSION_TEMPLATE = """# -*- coding: utf-8 -*-
# 由 tools/build_exe.py 自动生成 —— 手改会被下次打包覆盖
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(tup)s,
    prodvers=%(tup)s,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'Simiely'),
         StringStruct(u'FileDescription', u'Blender 无头渲染控制台'),
         StringStruct(u'FileVersion', u'%(ver)s'),
         StringStruct(u'InternalName', u'%(name)s'),
         StringStruct(u'LegalCopyright', u'MIT License'),
         StringStruct(u'OriginalFilename', u'%(name)s.exe'),
         StringStruct(u'ProductName', u'blender-render-console'),
         StringStruct(u'ProductVersion', u'%(ver)s')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def write_version_file(path, name):
    """生成 PyInstaller 的 `--version-file`。

    ⚠️ 这个文件是被 PyInstaller **eval** 的，所以内容必须是合法的 Python 字面量
    （`VSVersionInfo(...)` 那一坨），不是 ini/json。
    `080404b0` = 语言 0x0804（简体中文） + 代码页 0x04b0（1200 = Unicode），
    必须与 `VarFileInfo` 里的 `[2052, 1200]` 对得上，否则读版本号会返回空。
    """
    ver, tup = read_version()
    text = VERSION_TEMPLATE % {"tup": tup, "ver": ver, "name": name}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD), ("rgb", wintypes.DWORD * 4)]


_GDI_READY = False


def _init_gdi():
    """一次性把 User32/GDI32 的签名设好。

    ⚠️ 不设签名 = 参数按 `c_int` 传，64 位下句柄被截断，
    表现是 `ctypes.ArgumentError: OverflowError: int too long to convert`
    —— 报错看着像"参数类型不对"，其实根因是没声明 argtypes。
    """
    global _GDI_READY
    if _GDI_READY:
        return
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32

    u.PrivateExtractIconsW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
        wintypes.UINT, wintypes.UINT]
    u.PrivateExtractIconsW.restype = wintypes.UINT
    u.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(_ICONINFO)]
    u.GetIconInfo.restype = wintypes.BOOL
    u.GetDC.argtypes = [wintypes.HWND]
    u.GetDC.restype = wintypes.HDC
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.ReleaseDC.restype = ctypes.c_int
    u.DestroyIcon.argtypes = [wintypes.HICON]
    u.DestroyIcon.restype = wintypes.BOOL

    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.DeleteDC.argtypes = [wintypes.HDC]
    g.DeleteDC.restype = wintypes.BOOL
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteObject.restype = wintypes.BOOL
    g.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
    g.GetObjectW.restype = ctypes.c_int
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]
    g.GetDIBits.restype = ctypes.c_int
    _GDI_READY = True


def _exe_icon_rgba(path, size=32):
    """从 exe 里取出指定尺寸的图标，返回 (w, h, RGBA)，自上而下。

    用 `PrivateExtractIconsW` 而不是 `ExtractIconExW`：后者给的是"系统大图标"尺寸
    （随 DPI 变），没法跟 `assets/app.ico` 里那一层逐像素比。
    """
    _init_gdi()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hicon = wintypes.HICON()
    n = user32.PrivateExtractIconsW(path, 0, size, size, ctypes.byref(hicon),
                                    None, 1, 0)
    if n < 1 or not hicon.value:
        return None

    info = _ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    try:
        bmp = _BITMAP()
        if not gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bmp), ctypes.byref(bmp)):
            return None
        w, h = bmp.bmWidth, bmp.bmHeight

        hdc = user32.GetDC(None)
        memdc = gdi32.CreateCompatibleDC(hdc)

        class _INFO(ctypes.Structure):
            _fields_ = [("hdr", _BMIH), ("colors", wintypes.DWORD * 256)]

        bi = _INFO()
        bi.hdr.biSize = ctypes.sizeof(_BMIH)
        bi.hdr.biWidth = w
        bi.hdr.biHeight = -h            # 负数 = 自上而下
        bi.hdr.biPlanes = 1
        bi.hdr.biBitCount = 32
        bi.hdr.biCompression = 0        # BI_RGB

        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(memdc, info.hbmColor, 0, h, buf, ctypes.byref(bi), 0)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc)
        if not got:
            return None

        # BGRA → RGBA
        raw = bytearray(buf.raw)
        for i in range(0, len(raw), 4):
            raw[i], raw[i + 2] = raw[i + 2], raw[i]
        return w, h, bytes(raw)
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)


def _exe_file_version(path):
    """读 exe 的 FileVersion 字符串（纯 ctypes，不用 pywin32）。"""
    ver = ctypes.windll.version
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        wintypes.DWORD, ctypes.c_void_p]
    ver.GetFileVersionInfoW.restype = wintypes.BOOL
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    ver.VerQueryValueW.restype = wintypes.BOOL

    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return None

    ptr = ctypes.c_void_p()
    ln = wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                              ctypes.byref(ptr), ctypes.byref(ln)):
        return None
    words = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))
    lang, cp = words[0], words[1]

    ptr2 = ctypes.c_void_p()
    ln2 = wintypes.UINT()
    sub = "\\StringFileInfo\\%04x%04x\\FileVersion" % (lang, cp)
    if not ver.VerQueryValueW(buf, sub, ctypes.byref(ptr2), ctypes.byref(ln2)):
        return None
    return ctypes.wstring_at(ptr2, ln2.value).rstrip("\x00")


def check_icon(exe):
    """确认 exe 里的图标 == assets/app.ico 的 32px 那层（逐像素比对）。"""
    got = _exe_icon_rgba(exe, 32)
    if not got:
        log("  ✗ 读不出 exe 图标")
        return False
    w, h, rgba = got
    if (w, h) != (32, 32):
        log("  ✗ 图标尺寸不是 32x32（拿到 %dx%d），无法比对" % (w, h))
        return False

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import make_icon
    want = make_icon.render(32)
    diff = 0
    for i in range(len(want)):
        d = want[i] - rgba[i]
        diff += d if d >= 0 else -d
    avg = diff / float(len(want))
    ok = avg < 2.0
    log("  %s exe 图标与 assets/app.ico 一致（平均通道差 %.2f）" % ("✓" if ok else "✗", avg))
    return ok


def check_version(exe):
    """确认 exe 的版本资源与 `brconsole/__init__.py` 的 __version__ 一致。"""
    want, _ = read_version()
    got = _exe_file_version(exe)
    ok = bool(got) and got.startswith(want)
    log("  %s 版本资源 FileVersion = %s（期望 %s）" % ("✓" if ok else "✗", got, want))
    return ok


def write_spec(path, name, console, version_file):
    """生成 spec 文件。

    ⚠️ 不能用命令行拼 `--add-data`：sidecar 有几百个文件，命令行会超过 Windows 的
    长度上限（`WinError 206 文件名或扩展名太长`），必须走 spec + Tree()。
    """
    spec = """# -*- mode: python ; coding: utf-8 -*-
# 由 tools/build_exe.py 自动生成 —— 手改会被下次打包覆盖
a = Analysis(
    [%(entry)r],
    pathex=[%(root)r],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# exe 自带的 tkinter sidecar：<_MEIPASS>/_brc/{DLLs,Lib,tcl}
a.binaries += Tree(%(dlls)r, prefix=%(dlls_dst)r, typecode='BINARY')
a.datas += Tree(%(tkinter)r, prefix=%(tkinter_dst)r)
# ⚠️ 必须整目录打包 sidecar/tcl，不能只挑 tcl8.6/tk8.6：
# Tcl 的 auto_path 是 `[file dirname $tcl_library]`（= _brc/tcl），
# Windows 上 `package require dde / registry` 要找同级的 dde1.4 / reg1.3，
# `tcl8/8.x`（兼容 tclIndex）也在这一层。少一个就会在 tk.tcl 里报
# "can't find package dde"，且报错发生在 Tk 初始化中途，很难看出根因。
a.datas += Tree(%(tcl_root)r, prefix=%(tcl_root_dst)r)
# 内置假 Blender：让 `--demo` 自检在打包后照样能跑（没有独立的 python.exe 可以拉起来，
# 所以 main.py 会用 `--fake-blender` 把 exe 自己再当一次子进程）
# ⚠️ datas 条目的顺序是 **(目标名, 源路径, 类型)** —— 写反了不会报错，
#    条目照样进 TOC，但解包时不会落地（实测 `_brc/` 下只有 DLLs/Lib/tcl，没有 selftest）
a.datas += [(%(fake_dst_file)r, %(fake)r, 'DATA')]
# driver 源码：onefile 下 .py 模块在 PYZ 归档里、磁盘上没有，
# `core.read_driver_source()` 需要一份**真实文件**来注入给 Blender，所以单独当数据文件带一份
a.datas += [(%(driver_dst_file)r, %(driver)r, 'DATA')]
# 图标也进包：exe 自己的图标由下面的 icon= 写进 PE 资源，但**窗口**图标要运行时读文件
# （不设的话 Windows 上 Tk 窗口会顶着 Tk 自带的羽毛，跟 exe 图标对不上）
a.datas += [(%(icon_dst)r, %(icon)r, 'DATA')]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=%(name)r,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=%(console)r,
    disable_windowed_traceback=False,
    icon=%(icon)r,
    version=%(version)r,
)
""" % {
        "entry": ENTRY,
        "root": ROOT,
        "name": name,
        "console": bool(console),
        "dlls": os.path.join(SIDECAR, "DLLs"),
        "dlls_dst": FROZEN_SUBDIR + "/DLLs",
        "tkinter": os.path.join(SIDECAR, "Lib", "tkinter"),
        "tkinter_dst": FROZEN_SUBDIR + "/Lib/tkinter",
        "tcl_root": os.path.join(SIDECAR, "tcl"),
        "tcl_root_dst": FROZEN_SUBDIR + "/tcl",
        "fake": os.path.join(ROOT, "tests", "fake_blender.py"),
        "fake_dst": FROZEN_SUBDIR + "/selftest",
        "fake_dst_file": FROZEN_SUBDIR + "/selftest/fake_blender.py",
        "driver": os.path.join(ROOT, "brconsole", "driver.py"),
        "driver_dst_file": FROZEN_SUBDIR + "/py/driver.py",
        "icon": ICON,
        "icon_dst": FROZEN_SUBDIR + "/assets/app.ico",
        "version": version_file,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(spec)
    return path


def build(python, console=False, keep_build=False):
    name = CLI_NAME if console else GUI_NAME
    ver_file = write_version_file(
        os.path.join(ROOT, "build", name + "_version.txt"), name)
    spec = write_spec(os.path.join(ROOT, "build", name + ".spec"), name, console,
                      ver_file)
    args = [
        python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build", name),
        spec,
    ]
    log("打包 %s（%s）…" % (name, "console" if console else "windowed"))
    p = subprocess.run(args, cwd=ROOT)
    if p.returncode != 0:
        log("打包失败（rc=%s）" % p.returncode)
        return False

    exe = os.path.join(ROOT, "dist", name + ".exe")
    if not os.path.exists(exe):
        log("没找到产物：%s" % exe)
        return False
    size = os.path.getsize(exe) / (1024.0 * 1024.0)
    log("  ✓ %s（%.1f MB）" % (exe, size))
    if not keep_build:
        try:
            shutil.rmtree(os.path.join(ROOT, "build", name), ignore_errors=True)
        except Exception:
            pass
    return True


def verify(exe_dir):
    """打包后自检 —— 只看"打包成功"是不够的，要确认**运行时**真的拿得到东西。

    这里查的正是踩过的坑：sidecar 的 tcl 目录、内置假 Blender 到底有没有真的解包出来。
    （datas 条目顺序写反时，TOC 里照样有记录、构建也照样"成功"，但运行时找不到文件。）
    两个 exe 的数据一致，查保留控制台输出的那个就够。
    """
    exe = os.path.join(exe_dir, CLI_NAME + ".exe")
    if not os.path.exists(exe):
        log("  ✗ 自检跳过：没找到 %s" % exe)
        return False
    ok = True

    # 1) --help 能跑 → 解释器与包都进得去
    # ⚠️ 按**字节**匹配而不是按文本：Windows 上子进程的 stdout 走控制台代码页（GBK），
    #    用 text=True 按 UTF-8 解码会直接抛 UnicodeDecodeError（这是自检脚本自己的坑）。
    p = subprocess.run([exe, "--help"], capture_output=True, timeout=180)
    head = (p.stdout or b"") + (p.stderr or b"")
    has = b"--max-restarts" in head and b"--max-no-progress" in head
    log("  %s --help 正常且含新选项" % ("✓" if (p.returncode == 0 and has) else "✗"))
    ok &= (p.returncode == 0 and has)

    # 1b) 图标与版本资源：写进了 PE 才算数（spec 里对应 icon= / version=）
    ok &= check_icon(exe)
    ok &= check_version(exe)

    # 2) 内置假 Blender 能跑 → `_brc/selftest` 真的解包出来了
    work = os.path.join(ROOT, "build", "_verify")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    blender = os.path.join(work, "v.blend")
    with open(blender, "wb") as f:
        f.write(b"BLENDER-v5")
    job = os.path.join(work, "job.json")
    with open(job, "w", encoding="utf-8") as f:
        json.dump({"frames": [1, 2], "output_template": os.path.join(work, "out", "f_####"),
                   "blend": blender, "file_format": "PNG"}, f)
    p = subprocess.run([exe, "--fake-blender", "--job", job, "--sleep", "0.01"],
                       capture_output=True, timeout=180)
    out = (p.stdout or b"") + (p.stderr or b"")
    n = out.count(b"##PROG##")
    log("  %s 内置假 Blender 可运行（%d 行 ##PROG##）" % ("✓" if n >= 2 else "✗", n))
    if n < 2:
        log("    实际输出：%s" % out.decode("utf-8", "replace").strip()[:300])
    ok &= n >= 2

    # 3) **完整 core 流水线**：把 exe 自己当成 blender 拉起来跑一轮
    #    为什么必须走这一层：单跑假 Blender 绕过了 core，就测不到
    #    「driver 源码能不能注入」（onefile 下 .py 不在磁盘上，这是真踩过的坑）。
    #    用 .bat 做跳板：`<exe> -b 工程 -P driver ... -- job.json` 里的 job 路径会被
    #    main.py 的 `--fake-blender` 入口认出来。
    shim = os.path.join(work, "fake_blender.bat")
    with open(shim, "w", encoding="ascii", newline="") as f:
        f.write('@echo off\r\n"%s" --fake-blender %%*\r\n' % exe.replace("/", "\\"))
    outdir = os.path.join(work, "core")
    p = subprocess.run([exe, blender, "-s", "1", "-e", "3", "-E", "CYCLES",
                        "--blender", shim,
                        "-o", os.path.join(outdir, "c_####"),
                        "--state", os.path.join(outdir, ".state.json")],
                       capture_output=True, timeout=300)
    pngs = sorted(glob.glob(os.path.join(outdir, "*.png")))
    ok &= (p.returncode == 0 and len(pngs) == 3)
    log("  %s 完整流水线跑通（rc=%s，产出 %d 张）"
        % ("✓" if (p.returncode == 0 and len(pngs) == 3) else "✗", p.returncode, len(pngs)))
    if not (p.returncode == 0 and len(pngs) == 3):
        log("    实际输出：%s"
            % ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace").strip()[-400:])

    shutil.rmtree(work, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true", help="只打命令行版")
    ap.add_argument("--both", action="store_true", help="GUI 版 + 命令行版都打")
    ap.add_argument("--keep-build", action="store_true", help="保留 build/ 便于排错")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过打包后自检（默认会跑一遍，确认 sidecar 与内置假 Blender 真的解包出来了）")
    ap.add_argument("--python", help="指定用来打包的 Python（需已装 PyInstaller）")
    args = ap.parse_args()

    python = args.python or find_python()
    if not python:
        raise SystemExit(
            "没找到装了 PyInstaller 的 Python。\n"
            "  python -m venv <venv> && <venv>\\Scripts\\python -m pip install pyinstaller")
    check = subprocess.run([python, "-c", "import PyInstaller;print(PyInstaller.__version__)"],
                           capture_output=True, text=True)
    if check.returncode != 0:
        raise SystemExit("这个 Python 没装 PyInstaller：%s\n  %s -m pip install pyinstaller"
                         % (python, python))
    log("PyInstaller %s（%s）" % (check.stdout.strip(), python))
    check_sidecar()
    ensure_icon()
    log("  版本 %s（取自 brconsole/__init__.py）" % read_version()[0])
    for need, why in ((os.path.join(ROOT, "tests", "fake_blender.py"), "打包后 `--demo` 自检用"),
                      (os.path.join(ROOT, "brconsole", "driver.py"), "注入给 Blender 的驱动脚本")):
        if not os.path.exists(need):
            raise SystemExit("缺 %s —— %s" % (need, why))

    ok = True
    if args.console:
        ok = build(python, console=True, keep_build=args.keep_build)
    elif args.both:
        ok = build(python, console=False, keep_build=args.keep_build)
        ok &= build(python, console=True, keep_build=args.keep_build)
    else:
        ok = build(python, console=False, keep_build=args.keep_build)

    if ok and not args.no_verify:
        log("\n打包后自检：")
        ok &= verify(os.path.join(ROOT, "dist"))

    if ok:
        log("\n产物在 dist/ —— 界面版双击即可，命令行版示例：")
        log('  brc.exe 工程.blend -s 1 -e 240 -o out/frame_####')
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
