# -*- coding: utf-8 -*-
r"""探测 blender.exe。

⚠️ 不能用注册表：`reg.exe` 在本机安全策略黑名单里（AGENTS.md 关键坑 6）。
所以只做文件系统扫描 + 环境变量 + PATH，并且**自动探测失败后手填路径**是一等公民路径。

另外实测：`D:\BlenderPortable\5.2\` 只有 config/datafiles/scripts，是用户配置目录而
不是程序本体 —— 所以命中目录后必须确认 `blender.exe` 真的在里面。
"""

import os
import re
import shutil
import time

EXE = "blender.exe"

# 常见安装根目录（Blender 官方安装器默认落在 Blender Foundation 下）
COMMON_ROOTS = [
    r"C:\Program Files\Blender Foundation",
    r"C:\Program Files (x86)\Blender Foundation",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender Foundation"),
    os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Blender Foundation"),
]

# 便携版常见落点：盘符根下若干层
PORTABLE_HINTS = [
    r"BlenderPortable", r"Blender", r"blender", r"Portable", r"PortableApps",
]

RE_VER_IN_PATH = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

_SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "node_modules",
              ".git", "appdata", "programdata", "recovery"}


def _version_from_path(path):
    """从路径里抠版本号，用于给候选排序（新版优先）。"""
    best = None
    for part in os.path.normpath(path).split(os.sep):
        m = RE_VER_IN_PATH.search(part)
        if m:
            v = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
            if best is None or v > best:
                best = v
    return best or (0, 0, 0)


def scan_root(root, max_depth=3, deadline=None, limit=20):
    """在 root 下找 blender.exe，深度不超过 max_depth。带截止时间，免得扫半天。"""
    found = []

    def walk(d, depth):
        if deadline and time.time() > deadline:
            return
        if len(found) >= limit:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if len(found) >= limit or (deadline and time.time() > deadline):
                return
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth >= max_depth:
                        continue
                    if e.name.lower() in _SKIP_DIRS:
                        continue
                    walk(e.path, depth + 1)
                elif e.name.lower() == EXE:
                    found.append(e.path)
            except OSError:
                continue

    if root and os.path.isdir(root):
        walk(root, 0)
    return found


def find_blender(roots=None, deep=False, max_depth=3, timeout=8.0):
    """返回候选列表（新版在前）。

    deep=False 只扫常见安装目录（快，几乎瞬时）；
    deep=True  额外扫各盘符根下的便携版目录（慢，带超时）。
    """
    seen = set()
    out = []

    def add(p):
        if not p:
            return
        p = os.path.abspath(p)
        if p.lower() in seen or not os.path.exists(p):
            return
        seen.add(p.lower())
        out.append(p)

    # 1) 环境变量最优先：用户显式指定就是权威
    for var in ("BRC_BLENDER", "BLENDER_EXE"):
        add(os.environ.get(var))

    # 2) PATH
    try:
        add(shutil.which("blender") or shutil.which(EXE))
    except Exception:
        pass

    deadline = time.time() + timeout
    # 3) 常见安装目录
    for root in (COMMON_ROOTS if roots is None else roots):
        if time.time() > deadline:
            break
        for p in scan_root(root, max_depth=max_depth, deadline=deadline):
            add(p)

    # 4) 便携版：盘符根 + 提示目录
    if deep:
        for drive in _drives():
            if time.time() > deadline:
                break
            for p in scan_root(drive, max_depth=2, deadline=deadline, limit=40):
                add(p)
            for hint in PORTABLE_HINTS:
                if time.time() > deadline:
                    break
                for p in scan_root(os.path.join(drive, hint), max_depth=max_depth,
                                   deadline=deadline):
                    add(p)

    out.sort(key=lambda p: (_version_from_path(p), os.path.getmtime(p)), reverse=True)
    return out


def _drives():
    if os.name != "nt":
        return ["/"]
    drives = []
    for c in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        d = "%s:\\" % c
        if os.path.isdir(d):
            drives.append(d)
    return drives


def resolve_blender(explicit=None, deep=False):
    """给 CLI 用：显式路径优先，否则自动探测。返回 (path, candidates)。"""
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit("指定的 blender.exe 不存在：%s" % explicit)
        return os.path.abspath(explicit), [os.path.abspath(explicit)]
    cands = find_blender(deep=deep)
    return (cands[0] if cands else None), cands
