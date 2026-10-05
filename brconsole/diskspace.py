# -*- coding: utf-8 -*-
"""开工前的磁盘空间预检。

为什么要有这一条：输出目录是用户自己填的，几百上千帧写下去能把盘填满。
**系统盘写满会让 Windows 卡在开机或登录**（pagefile、用户配置文件、Temp 都写不进去），
这是本程序唯一一条能导致"进不去系统"的路径 —— 所以宁可开工前拦一道，也别等它发生。

判据分两档，**都建立在确定性事实上，不猜压缩率**：

| 档 | 条件 | 依据 |
|---|---|---|
| `block` | 可用空间低于该盘的**最低水位** | 系统盘 10 GiB（Windows 自身要留 10~15% 或 ≥20 GB，低于 10 GB 会明显不稳）；其它盘 2 GiB（只是别在已经满了的盘上硬渲） |
| `warn` | 可用空间低于**未压缩上界**估算的产出总量 | 上界 = 宽×高×每像素字节×帧数，任何格式都超不过它 —— 所以这条只可能**偏早**提醒、不会漏 |

取不到空间信息时**一律放行**（`None` 当"不知道"）：拦不住的代价远小于误拦。
"""

import os
import shutil

MIB = 1024 ** 2
GIB = 1024 ** 3

# 各盘的"最低水位"，低于它就拒绝开始
MIN_FREE_SYSTEM = 10 * GIB
MIN_FREE_OTHER = 2 * GIB

# 每像素字节数 —— 全部按**未压缩上界**取，只用于估"最多可能写多少"：
# 8 位 RGBA = 4；OpenEXR 是 half-float RGBA = 8。
# 认不出的格式（含「保持工程设置」）按 8 走：不知道工程里是什么格式，宁可按大的估。
BYTES_PER_PIXEL = {"PNG": 4, "JPEG": 4, "TIFF": 4, "OPEN_EXR": 8}
DEFAULT_BYTES_PER_PIXEL = 8


# ---------------- 卷与剩余空间 ----------------
def drive_key(path):
    """路径所在的**卷标识**，用于和 `SystemDrive` 比。

    普通盘归一成 `C:\\` 这种形式；UNC（`\\\\server\\share`）原样保留 ——
    `os.path.splitdrive` 对 UNC 返回的已经是共享名，再加盘符是错的。
    """
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return ""
    if drive.startswith("\\\\"):
        return drive.upper()
    return drive.rstrip("\\/").upper() + "\\"


def is_system_drive(path):
    """输出目录是不是落在系统盘上（系统盘的水位高得多）。"""
    return drive_key(path) == drive_key(os.environ.get("SystemDrive") or "C:\\")


def nearest_existing_dir(path):
    """从 `path` 往上找到第一个真实存在的目录。

    ⚠️ `shutil.disk_usage` 在 Windows 上**要求传入目录**（不存在的路径会抛
    `FileNotFoundError`），而输出目录常常是用户刚填、还没建的 —— 所以要先降级到
    最近的已存在祖先。拿到的仍然是同一个卷的剩余空间，不影响结论。
    """
    p = os.path.abspath(path)
    while True:
        if os.path.isdir(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:                     # 已经到根了还是不存在
            return None
        p = parent


def _real_free(path):
    """真实探测：先把路径降级到**已存在的目录**，再问系统要该卷的可用字节数。

    ⚠️ `shutil.disk_usage` 在 Windows 上**要求传入目录**（不存在的路径会抛
    `FileNotFoundError`），而输出目录常常是用户刚填、还没建的 —— 所以必须先降级到
    最近的已存在祖先。拿到的仍然是同一个卷的剩余空间，不影响结论。
    """
    target = nearest_existing_dir(path)
    if target is None:
        return None
    try:
        return int(shutil.disk_usage(target).free)
    except OSError:
        return None


def free_bytes(path, free_fn=None):
    """`path` 所在卷的**可用**字节数（对当前用户可用，比"总空闲"更保守）。

    取不到（路径怪、权限、后端不支持）返回 `None` —— 调用方必须把 `None` 当
    "不知道"并放行。`free_fn` 只用于单测注入，签名 `(输出目录) -> int|None`；
    注意它拿到的是**原始路径**（不经过 `nearest_existing_dir`），这样测试才能
    凭空造出「别的盘」「不存在的盘」来验规则，不必去插一根真的 U 盘。
    """
    probe = free_fn or _real_free
    try:
        got = probe(path)
    except Exception:                       # noqa: BLE001 —— 探测失败不能拖垮开工
        return None
    return None if got is None else max(0, int(got))


# ---------------- 产出量估算 ----------------
def frame_pixels(resolution, percentage=None):
    """单帧像素数；分辨率未知/非法返回 `None`。"""
    if not resolution:
        return None
    try:
        w, h = int(resolution[0]), int(resolution[1])
    except (TypeError, ValueError, IndexError):
        return None
    if w <= 0 or h <= 0:
        return None
    if percentage:
        try:
            pct = float(percentage)
        except (TypeError, ValueError):
            pct = 100.0
        if pct > 0 and pct != 100:
            w = max(1, int(round(w * pct / 100.0)))
            h = max(1, int(round(h * pct / 100.0)))
    return w * h


def estimate_bytes(frames, resolution=None, percentage=None, file_format=None):
    """这次渲染**最多**可能写多少字节（未压缩上界）；算不出来返回 `None`。"""
    try:
        n = len(list(frames or ()))
    except TypeError:
        return None
    px = frame_pixels(resolution, percentage)
    if not n or not px:
        return None
    per_px = BYTES_PER_PIXEL.get((file_format or "").upper(), DEFAULT_BYTES_PER_PIXEL)
    return n * px * per_px


def human(n):
    """给用户看的体积。"""
    if n is None:
        return "未知"
    if n >= GIB:
        return "%.1f GB" % (n / float(GIB))
    if n >= MIB:
        return "%.0f MB" % (n / float(MIB))
    return "%.0f KB" % (n / 1024.0)


# ---------------- 预检 ----------------
def check(cfg, free_fn=None):
    """开工前的空间预检。返回 `(level, message)`：

    - `"block"` 别开工（message 是给用户看的整句话）
    - `"warn"`  能开工，但该提醒一句
    - `"ok"`    正常（message 为空串）

    只看输出目录所在的盘 —— 断点文件、日志都在同一处，跟着输出走。
    """
    out = getattr(cfg, "output_template", None) or ""
    if not out:
        return "ok", ""
    folder = os.path.dirname(os.path.abspath(out)) or "."
    free = free_bytes(folder, free_fn=free_fn)
    if free is None:
        return "ok", ""

    on_system = is_system_drive(folder)
    floor = MIN_FREE_SYSTEM if on_system else MIN_FREE_OTHER

    if free < floor:
        if on_system:
            why = ("系统盘写满会让 Windows 卡在开机或登录（pagefile、用户配置文件、"
                   "Temp 都写不进去），所以这里留了 %s 的余量。" % human(floor))
        else:
            why = "盘上只剩这点空间，这次渲染必然把盘写满，中途失败比现在拦住更麻烦。"
        return "block", (
            "输出目录在%s，可用空间只剩 %s，低于最低水位 %s。\n%s\n"
            "请先腾空间，或把「输出路径」改到别的盘。\n目录：%s"
            % ("系统盘" if on_system else "其它盘", human(free), human(floor), why, folder))

    frames = list(getattr(cfg, "frames", None) or ())
    # ⚠️ 已知局限：宽高留空（「保持工程设置」）时算不出产出量，这一档就**不提醒**。
    #    宁可不说，也不编一个数字出来 —— 水位那档仍然照常生效。
    need = estimate_bytes(frames, getattr(cfg, "resolution", None),
                          getattr(cfg, "resolution_percentage", None),
                          getattr(cfg, "file_format", None))
    if need is not None and free < need:
        return "warn", (
            "输出目录在%s，可用空间 %s；这次 %d 帧最多可能写到 %s"
            "（按未压缩上限估，PNG/JPEG 实际通常小得多）。\n"
            "建议先确认空间够，或把「输出路径」改到别的盘。"
            % ("系统盘" if on_system else "其它盘", human(free), len(frames), human(need)))
    return "ok", ""
