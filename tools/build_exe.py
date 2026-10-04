# -*- coding: utf-8 -*-
r"""打包成单个 exe（PyInstaller）。

    python tools/build_exe.py                # 打 GUI 版（--windowed，双击用界面）
    python tools/build_exe.py --console      # 打命令行版（保留控制台输出）
    python tools/build_exe.py --both         # 两个都打
    python tools/build_exe.py --keep-build   # 保留 build/ 便于排错

产出在 `dist/`：
    blender-render-console.exe   图形界面版（--windowed）
    brc.exe                      命令行版（--console，输出能打到终端）

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
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIDECAR = os.path.join(ROOT, "sidecar")
ENTRY = os.path.join(ROOT, "main.py")

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


def write_spec(path, name, console):
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
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(spec)
    return path


def build(python, console=False, keep_build=False):
    name = CLI_NAME if console else GUI_NAME
    spec = write_spec(os.path.join(ROOT, "build", name + ".spec"), name, console)
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
