# -*- coding: utf-8 -*-
r"""抓一张全屏 PNG —— 用来**肉眼验证 GUI 布局**（只靠读代码看不出错位/挤成一团）。

    python tools/capture_screen.py C:\Temp\brc_gui.png

为什么不用现成工具：沙箱里 PowerShell 的 `Add-Type` 被安全策略拦掉
（"Add-Type compiles and loads .NET code at runtime"），装不上 Pillow 也不值当。
于是直接用 ctypes 调 GDI：桌面 DC → 兼容位图 → GetDIBits 取像素 → 手写 PNG
（zlib 压缩，标准库就够，无第三方依赖）。
"""

import ctypes
import struct
import sys
import zlib
from ctypes import wintypes

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


def grab(window_title=None):
    """返回 (width, height, RGB 字节串，按上到下排列)。

    `window_title` 给定时只抓那个窗口（用 PrintWindow，**被别的窗口挡住也能抓到**，
    做 UI 验证时比全屏截图可靠得多）。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    P = ctypes.c_void_p          # 句柄统一按指针传，否则 64 位下会被截断成 int 溢出
    I = ctypes.c_int
    user32.GetDC.argtypes = [P]
    user32.GetDC.restype = P
    user32.ReleaseDC.argtypes = [P, P]
    user32.FindWindowW.argtypes = [P, ctypes.c_wchar_p]
    user32.FindWindowW.restype = P
    user32.GetWindowRect.argtypes = [P, ctypes.POINTER(wintypes.RECT)]
    user32.PrintWindow.argtypes = [P, P, wintypes.UINT]
    user32.SetForegroundWindow.argtypes = [P]
    gdi32.CreateCompatibleDC.argtypes = [P]
    gdi32.CreateCompatibleDC.restype = P
    gdi32.CreateCompatibleBitmap.argtypes = [P, I, I]
    gdi32.CreateCompatibleBitmap.restype = P
    gdi32.SelectObject.argtypes = [P, P]
    gdi32.SelectObject.restype = P
    gdi32.DeleteObject.argtypes = [P]
    gdi32.DeleteDC.argtypes = [P]
    gdi32.BitBlt.argtypes = [P, I, I, I, I, P, I, I, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [P, P, wintypes.UINT, wintypes.UINT, P,
                                ctypes.POINTER(BITMAPINFO), wintypes.UINT]

    hwnd = None
    if window_title:
        hwnd = user32.FindWindowW(None, window_title)
        if not hwnd:
            raise SystemExit("找不到窗口：%s" % window_title)
        user32.SetForegroundWindow(hwnd)

    if hwnd:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
    else:
        # 多显示器时用虚拟屏幕尺寸，免得只截到主屏一角
        w = user32.GetSystemMetrics(78) or user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(79) or user32.GetSystemMetrics(1)
        x = user32.GetSystemMetrics(76)
        y = user32.GetSystemMetrics(77)

    hdc = user32.GetDC(hwnd or 0)
    if not hdc:
        raise SystemExit("GetDC 失败")
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    if hwnd:
        # PW_RENDERFULLCONTENT：抓到的是窗口自身内容，遮挡不影响
        if not user32.PrintWindow(hwnd, mem, 0x00000002):
            raise SystemExit("PrintWindow 失败")
    elif not gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY):
        raise SystemExit("BitBlt 失败")

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负值 = 自上而下，省一次翻转
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    # biBitCount=32 → 每行就是 w*4 字节（天然 4 字节对齐，别按 24 位算 stride，
    # 缓冲区给小了 GetDIBits 会直接返回 0）
    stride = w * 4
    buf = ctypes.create_string_buffer(stride * h)
    got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    if not got:
        raise SystemExit("GetDIBits 失败")

    raw = buf.raw
    rows = []
    for r in range(h):
        row = raw[r * stride:r * stride + w * 4]
        rows.append(bytes(bgra_to_rgb(row)))
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd or 0, hdc)
    return w, h, b"".join(rows)


def bgra_to_rgb(row):
    out = bytearray(len(row) // 4 * 3)
    i = j = 0
    while i < len(row):
        b, g, r = row[i], row[i + 1], row[i + 2]
        out[j] = r
        out[j + 1] = g
        out[j + 2] = b
        i += 4
        j += 3
    return out


def write_png(path, w, h, rgb):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + rgb[r * w * 3:(r + 1) * w * 3] for r in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def pixel(rgb, w, x, y):
    i = (y * w + x) * 3
    return "#%02x%02x%02x" % (rgb[i], rgb[i + 1], rgb[i + 2])


def main():
    args = [a for a in sys.argv[1:]]
    title = None
    probes = []
    for flag in ("--window", "--probe"):
        while flag in args:
            i = args.index(flag)
            val = args[i + 1]
            del args[i:i + 2]
            if flag == "--window":
                title = val
            else:
                # --probe "x,y;x,y"：采样几个点的颜色，用来核对主题配色有没有生效
                probes = [tuple(int(v) for v in p.split(",")) for p in val.split(";")]
    path = args[0] if args else "screen.png"
    w, h, rgb = grab(window_title=title)
    write_png(path, w, h, rgb)
    print("%s（%dx%d）" % (path, w, h))
    for (x, y) in probes:
        if 0 <= x < w and 0 <= y < h:
            print("  像素 (%d,%d) = %s" % (x, y, pixel(rgb, w, x, y)))


if __name__ == "__main__":
    main()
