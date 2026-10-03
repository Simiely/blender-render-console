#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_tkinter.py — 从 python.org 官方安装包提取 tcl/tk，拼装隔离的 tkinter sidecar。

背景：本机托管 Python 3.13.12 不含 tkinter，install_binary 不可用，且 MSI 服务被沙箱
限制（0x80070643）导致静默安装走不通。因此改为「纯文件提取」路线：
    PE(burn bundle) → 附加流 → CAB 容器 → 各组件 MSI → 嵌套 cab1.cab → 实际文件

产出的 sidecar 目录结构：
    sidecar/DLLs/           _tkinter.pyd, tcl86t.dll, tk86t.dll
    sidecar/Lib/tkinter/    tkinter 包（__init__.py, ttk.py, ...）
    sidecar/tcl/tcl8.6/     Tcl 脚本库
    sidecar/tcl/tk8.6/      Tk 脚本库

用法：
    python tools/build_tkinter.py            # 构建 + 自检
    python tools/build_tkinter.py --verify   # 只自检

⚠️ 已知取舍：tzdata/（时区数据）与 nmake/（构建辅助）被跳过。
   这两处是唯一无法可靠还原目录结构的部分（文件名里的下划线无法唯一反推路径），
   且对 GUI 渲染工具没有用途。
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys
import urllib.request

PY_VER = "3.13.12"
MIRRORS = [
    "https://mirrors.huaweicloud.com/python/{v}/python-{v}-amd64.exe",
    "https://mirrors.aliyun.com/python-release/windows/python-{v}-amd64.exe",
    "https://www.python.org/ftp/python/{v}/python-{v}-amd64.exe",
]
SEVEN_ZIP_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    "7z",
]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, "_tkbuild")
SIDECAR = os.path.join(ROOT, "sidecar")

# 还原规则用到的白名单（来源：实测 1225 个扁平文件名的段分布统计）
TCL_ROOTS = ["tcl8.6", "tk8.6", "tcl8", "tk8", "dde1.4", "reg1.3"]
TCL_SUBDIRS = {
    "tcl8.6": ["encoding", "http", "msgs", "opt0.4", "package"],
    "tk8.6": ["demos", "images", "msgs", "ttk"],
}
SKIP_SEGMENTS = ("tzdata", "nmake")  # 无法可靠还原，且无用途


def log(msg):
    print(msg, flush=True)


def find_7z():
    for c in SEVEN_ZIP_CANDIDATES:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    raise SystemExit("找不到 7-Zip，请安装后重试（或修改 SEVEN_ZIP_CANDIDATES）")


def run7z(seven_zip, *args, check=True):
    p = subprocess.run([seven_zip] + list(args), capture_output=True, text=True,
                       errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("7z 失败: %s\n%s" % (" ".join(args), p.stdout[-2000:]))
    return p.stdout


def list_entries(seven_zip, archive):
    out = run7z(seven_zip, "l", "-ba", archive)
    names = []
    for line in out.splitlines():
        parts = line.split()
        if parts:
            names.append(parts[-1])
    return names


def download():
    os.makedirs(WORK, exist_ok=True)
    dst = os.path.join(WORK, "python-%s-amd64.exe" % PY_VER)
    if os.path.exists(dst) and os.path.getsize(dst) > 20 * 1024 * 1024:
        log("已存在安装包，跳过下载：%s" % dst)
        return dst

    last_err = None
    for tpl in MIRRORS:
        url = tpl.format(v=PY_VER)
        try:
            log("下载 %s" % url)
            with urllib.request.urlopen(url, timeout=120) as r, open(dst, "wb") as f:
                shutil.copyfileobj(r, f)
            log("  完成，%.1f MB" % (os.path.getsize(dst) / 1048576))
            return dst
        except Exception as e:  # noqa: BLE001
            last_err = e
            log("  失败：%s" % e)
    raise SystemExit("所有镜像都下载失败：%s" % last_err)


def pe_image_size(data):
    """读 PE 可选头里的 SizeOfImage，它之后的字节就是 overlay（附加数据）。"""
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e_lfanew + 24  # PE 签名 + COFF 头(20)
    return struct.unpack_from("<I", data, opt + 56)[0]


def carve_attached_container(exe_path):
    """burn bundle 的载荷藏在 PE overlay 里，定位 overlay 内第一个 CAB 签名并切出。"""
    with open(exe_path, "rb") as f:
        data = f.read()
    image_size = pe_image_size(data)
    start, pos = None, 0
    while True:
        i = data.find(b"MSCF", pos)
        if i < 0:
            break
        if i >= image_size:
            start = i
            break
        pos = i + 4
    if start is None:
        raise SystemExit("未在安装包 overlay 中找到 CAB 容器")
    out = os.path.join(WORK, "payload.cab")
    with open(out, "wb") as f:
        f.write(data[start:])
    log("PE ImageSize=%d，overlay 内 CAB 起点=%d" % (image_size, start))
    log("切出附加容器：payload.cab（%.1f MB）" % ((len(data) - start) / 1048576))
    return out


def find_tcltk_msi(seven_zip, payload_cab):
    """tcltk 组件的特征：MSI 内含嵌套 cab1.cab，且条目里出现 Icon.idle.exe。"""
    ex_dir = os.path.join(WORK, "payload")
    if not os.path.isdir(ex_dir):
        os.makedirs(ex_dir, exist_ok=True)
        run7z(seven_zip, "x", "-y", "-o" + ex_dir, payload_cab)

    blocks = sorted(os.listdir(ex_dir))
    for name in blocks:
        path = os.path.join(ex_dir, name)
        if not os.path.isfile(path):
            continue
        entries = list_entries(seven_zip, path)
        if "cab1.cab" in entries and any("idle.exe" in e.lower() for e in entries):
            log("定位到 tcltk 组件：%s（%d 个条目）" % (name, len(entries)))
            return path
    raise SystemExit("未能在载荷中找到 tcltk 组件")


def flatten_files(seven_zip, msi_path):
    msi_ex = os.path.join(WORK, "tcltk_msi")
    flat = os.path.join(WORK, "flat")
    if not os.path.isdir(msi_ex):
        os.makedirs(msi_ex, exist_ok=True)
        run7z(seven_zip, "x", "-y", "-o" + msi_ex, msi_path)
    inner = os.path.join(msi_ex, "cab1.cab")
    if not os.path.exists(inner):
        raise SystemExit("MSI 中未找到嵌套的 cab1.cab")
    if not os.path.isdir(flat):
        os.makedirs(flat, exist_ok=True)
        run7z(seven_zip, "x", "-y", "-o" + flat, inner)
    names = sorted(os.listdir(flat))
    log("解出扁平文件 %d 个" % len(names))
    return flat, names


def map_flat_name(name):
    """把扁平名还原成 sidecar 内的相对路径；返回 None 表示跳过。"""
    # 1) 根级二进制 —— 必须整包收下：tcl86t.dll 自身还依赖 zlib1.dll，
    #    只收 tcl86t/tk86t/_tkinter 会导致 tcl86t 加载时报"找不到指定的模块"。
    #    注意 _tkinter.pyd 文件名自带下划线，不能用"是否含下划线"来判定。
    if name.lower().endswith((".dll", ".pyd")) and not name.startswith(("Lib_", "tcl_")):
        return os.path.join("DLLs", name)

    # 2) tkinter 包（扁平结构，规则唯一确定）
    if name.startswith("Lib_tkinter_"):
        return os.path.join("Lib", "tkinter", name[len("Lib_tkinter_"):])

    # 3) tcl / tk 脚本库
    if name.startswith("tcl_"):
        rest = name[len("tcl_"):]
        if any(seg in rest.split("_")[:2] for seg in SKIP_SEGMENTS):
            return None
        for root in TCL_ROOTS:
            if rest == root or rest.startswith(root + "_"):
                tail = rest[len(root):].lstrip("_")
                if not tail:
                    return None
                for sub in TCL_SUBDIRS.get(root, []):
                    if tail.startswith(sub + "_"):
                        return os.path.join("tcl", root, sub,
                                            tail[len(sub) + 1:])
                return os.path.join("tcl", root, tail)
        # 根级文件（tclConfig.sh 之类）
        if "." in rest and "_" not in rest:
            return os.path.join("tcl", rest)
        return None

    return None


def restore(flat_dir, names):
    if os.path.isdir(SIDECAR):
        shutil.rmtree(SIDECAR)
    kept, skipped = 0, 0
    for name in names:
        rel = map_flat_name(name)
        if not rel:
            skipped += 1
            continue
        dst = os.path.join(SIDECAR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(flat_dir, name), dst)
        kept += 1
    log("还原 %d 个文件，跳过 %d 个" % (kept, skipped))

    # 关键一步：Python 3.8+ 在 Windows 上不再用 PATH 搜索扩展模块的依赖 DLL。
    # 注册 sitecustomize，让任何把 sidecar/Lib 放进 PYTHONPATH 的解释器自动生效。
    lib_dir = os.path.join(SIDECAR, "Lib")
    os.makedirs(lib_dir, exist_ok=True)
    with open(os.path.join(lib_dir, "sitecustomize.py"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(
            "# 由 tools/build_tkinter.py 生成：让 sidecar 的 tcl/tk 运行时可见\n"
            "import os\n"
            "_LIB = os.path.dirname(os.path.abspath(__file__))\n"
            "_ROOT = os.path.dirname(_LIB)\n"
            "_DLLS = os.path.join(_ROOT, 'DLLs')\n"
            "if os.path.isdir(_DLLS):\n"
            "    try:\n"
            "        os.add_dll_directory(_DLLS)\n"
            "    except (AttributeError, OSError):\n"
            "        pass\n"
            "    os.environ['PATH'] = _DLLS + os.pathsep + os.environ.get('PATH', '')\n"
            "for _v, _p in (('TCL_LIBRARY', 'tcl/tcl8.6'), ('TK_LIBRARY', 'tcl/tk8.6')):\n"
            "    _d = os.path.join(_ROOT, *_p.split('/'))\n"
            "    if os.path.isdir(_d):\n"
            "        os.environ.setdefault(_v, _d)\n")

    for key in ("DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll",
                "Lib/tkinter/__init__.py", "Lib/tkinter/ttk.py",
                "tcl/tcl8.6/init.tcl", "tcl/tk8.6/tk.tcl",
                "Lib/sitecustomize.py"):
        p = os.path.join(SIDECAR, key)
        log("  [%s] %s" % ("OK" if os.path.exists(p) else "缺失", key))
    return SIDECAR


def verify(python_exe=None):
    """自检：用 sidecar 的路径前缀跑一次 import tkinter 并实例化 Tk。"""
    python_exe = python_exe or sys.executable
    env = dict(os.environ)
    dlls = os.path.join(SIDECAR, "DLLs").replace("\\", "/")
    lib = os.path.join(SIDECAR, "Lib")
    env["PYTHONPATH"] = lib + os.pathsep + os.path.join(SIDECAR, "DLLs")
    env["TCL_LIBRARY"] = os.path.join(SIDECAR, "tcl", "tcl8.6")
    env["TK_LIBRARY"] = os.path.join(SIDECAR, "tcl", "tk8.6")
    env["PATH"] = os.path.join(SIDECAR, "DLLs") + os.pathsep + env.get("PATH", "")
    code = (
        "import os; os.add_dll_directory(r'%s');"
        "import tkinter;"
        "print('tkinter OK, TkVersion =', tkinter.TkVersion);"
        "r = tkinter.Tk(); r.withdraw(); r.update(); r.destroy();"
        "print('Tk() 实例化 + 销毁 OK')" % dlls
    )
    p = subprocess.run([python_exe, "-c", code], capture_output=True, text=True,
                       errors="replace", env=env)
    log(p.stdout.strip())
    if p.returncode != 0:
        log("自检失败：\n" + (p.stderr or "")[-3000:])
        return False
    log("自检通过")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="只做自检")
    args = ap.parse_args()

    if args.verify:
        sys.exit(0 if verify() else 1)

    seven_zip = find_7z()
    exe = download()
    cab = carve_attached_container(exe)
    msi = find_tcltk_msi(seven_zip, cab)
    flat_dir, names = flatten_files(seven_zip, msi)
    restore(flat_dir, names)
    log("\nsidecar 位置：%s" % SIDECAR)
    sys.exit(0 if verify() else 1)


if __name__ == "__main__":
    main()
