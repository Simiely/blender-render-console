# -*- coding: utf-8 -*-
r"""生成 assets/app.ico —— 纯标准库，不装 Pillow。

    python tools/make_icon.py                       # 写到 assets/app.ico
    python tools/make_icon.py --preview auto        # 顺便导一张 256 的 PNG
    python tools/make_icon.py --sheet sheet.png     # 跨尺寸自查图（改完图标必看）

为什么手写而不是找现成图标/装 Pillow：
1. 项目约定「除 sidecar 外不引第三方运行时依赖」，打包体积也要控；
2. 需求很简单 —— 一个在 **16px 下也认得出**的应用图标；
3. 做法是「矢量形状 + 4× 超采样 + box 降采样」自己画，再按 ICO 规范拼容器。

图形：成角渐变橙底 + 近黑的播放三角（橙 = Blender 的橙，三角 = 开始渲染）。

⚠️ 小尺寸单独调过（`profile()`）：16px 只有 256 个像素，把 256 那张等比缩下去
   会得到"一个糊掉的圆" —— 圆角被抗锯齿啃没、三角只剩几个点。所以 ≤40px
   改成「铺满 + 小圆角 + 放大的三角」。改图标后务必 `--sheet` 扫一眼再打包。
⚠️ 输出一律走 ASCII，别在这个脚本里打 emoji/箭头：Windows 控制台是 cp936，
   非 ASCII 符号会 UnicodeEncodeError（本项目已经栽过一次，见 CHANGELOG）。
"""

import argparse
import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, "assets", "app.ico")

# ---------------------------------------------------------------- 配色
TOP = (0xF5, 0x9A, 0x3C)        # 上：亮橙
BOTTOM = (0xCE, 0x5F, 0x16)     # 下：暗橙（成角渐变，避免扁平）
INK = (0x1B, 0x1B, 0x1F)        # 三角：近黑

SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4                          # 超采样倍率（16 样本/像素）

# ---------------------------------------------------------------- 形状（归一化坐标，中心为原点，边长 1）
BOX_HALF = 0.480                # 圆角方外框半宽（留 2% 边距）
BOX_RADIUS = 0.215              # 圆角半径
# 播放三角：bbox 中心右移 0.035 —— 三角形的视觉重心偏左，等宽居中看起来会"歪"
TRI = ((-0.145, -0.265), (0.215, 0.0), (-0.145, 0.265))
TRI_R = 0.028                   # 顶点圆角


def _scale_tri(tri, k):
    return tuple((x * k, y * k) for x, y in tri)


def profile(size):
    """按尺寸分档的形状参数 —— 小图标不能直接缩放大图标。

    16px 只有 256 个像素，圆角方会被抗锯齿啃成近似圆形、三角几乎糊掉，
    必须改成「铺满 + 小圆角 + 放大的三角」才认得出来。
    返回 (外框半宽, 圆角半径, 三角顶点, 三角圆角)。
    """
    if size <= 20:                       # 16
        return 0.500, 0.105, _scale_tri(TRI, 1.30), TRI_R * 1.4
    if size <= 28:                       # 24
        return 0.500, 0.150, _scale_tri(TRI, 1.16), TRI_R * 1.2
    if size <= 40:                       # 32
        return 0.490, 0.195, _scale_tri(TRI, 1.06), TRI_R * 1.1
    return BOX_HALF, BOX_RADIUS, TRI, TRI_R


def _round_rect(x, y, half, radius):
    """点是否在圆角方内（圆角矩形 SDF 的布尔形式）。"""
    qx = abs(x) - (half - radius)
    qy = abs(y) - (half - radius)
    if qx <= 0.0 and qy <= 0.0:
        return True
    dx = qx if qx > 0.0 else 0.0
    dy = qy if qy > 0.0 else 0.0
    return dx * dx + dy * dy <= radius * radius


def _triangle(x, y, tri, tri_r):
    """点是否在（带圆角的）三角内 —— 凸多边形内部 = 到各边有向距离的最小值 >= r。

    有向距离按「顶点顺序的左法线」取；这个顶点顺序下左法线指向内部（已用重心验证）。
    """
    md = 1e9
    for i in range(3):
        ax, ay = tri[i]
        bx, by = tri[(i + 1) % 3]
        ex, ey = bx - ax, by - ay
        inv = 1.0 / math.hypot(ex, ey)
        d = (x - ax) * (-ey * inv) + (y - ay) * (ex * inv)
        if d < md:
            md = d
    return md >= tri_r


def _shade(x, y, half, radius, tri, tri_r):
    """单个样本的颜色 (r,g,b,a)。"""
    if not _round_rect(x, y, half, radius):
        return (0, 0, 0, 0)
    if _triangle(x, y, tri, tri_r):
        return INK + (255,)
    t = y + 0.5                     # 0 = 顶部, 1 = 底部
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return (int(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
            int(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
            int(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
            255)


def render(size):
    """渲染一个尺寸，返回 RGBA 字节（自上而下、行优先）。"""
    step = 1.0 / (size * SS)
    half = 0.5
    bh, br, tri, trr = profile(size)
    buf = bytearray(size * size * 4)
    n = SS * SS
    idx = 0
    for py in range(size):
        for px in range(size):
            ar = ag = ab = aa = 0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * step - half
                for sx in range(SS):
                    x = (px * SS + sx + 0.5) * step - half
                    r, g, b, a = _shade(x, y, bh, br, tri, trr)
                    ar += r
                    ag += g
                    ab += b
                    aa += a
            buf[idx] = ar // n
            buf[idx + 1] = ag // n
            buf[idx + 2] = ab // n
            buf[idx + 3] = aa // n
            idx += 4
    return bytes(buf)


# ---------------------------------------------------------------- 编码
def _dib(size, rgba):
    """BITMAPINFOHEADER + XOR(BGRA, 自下而上) + AND 掩码（全 0，靠 alpha 透明）。"""
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    px = bytearray()
    for y in range(size - 1, -1, -1):
        row = rgba[y * size * 4:(y + 1) * size * 4]
        for i in range(0, len(row), 4):
            px += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))
    mask_row = bytes(((size + 31) // 32) * 4)
    return hdr + bytes(px) + mask_row * size


def _png(w, h, rgba):
    """最简 PNG（8bit RGBA，无交错，filter 0）。"""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * w * 4:(y + 1) * w * 4]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def build_ico(sizes=None):
    sizes = list(sizes or SIZES)
    imgs = []
    for s in sizes:
        rgba = render(s)
        imgs.append((s, _png(s, s, rgba) if s >= 256 else _dib(s, rgba)))

    out = struct.pack("<HHH", 0, 1, len(imgs))
    offset = 6 + 16 * len(imgs)
    entries = bytearray()
    for s, payload in imgs:
        dim = 0 if s >= 256 else s        # 256 在 ICO 目录里用 0 表示
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
    return out + bytes(entries) + b"".join(p for _, p in imgs), [s for s, _ in imgs]


def build_sheet(sizes=None, zoom=4, pad=8):
    """自查图：把各尺寸放大到同一倍数并排，浅底一排、深底一排。

    ⚠️ 光看 256 那张是会骗人的 —— 16px 才是任务栏/资源管理器里真正看到的那个。
    每次改图标都该扫一眼这张图再打包。
    """
    sizes = list(sizes or SIZES)
    # 只画小尺寸：128/256 那种大图肉眼看不出问题，反而把画布撑得很大
    small = [s for s in sizes if s <= 64] or [sizes[-1]]
    dark = [s for s in small if s >= 32][:3] or small[-1:]
    cell = max(small) * zoom
    w = pad + sum(s * zoom + pad for s in small)
    # 下排高度按**最大的那个**算，否则大的会被画布裁掉（踩过）
    h = pad + cell + pad + max(dark) * zoom + pad

    canvas = bytearray(b"\xf3\xf3\xf3\xff" * (w * (pad + cell + pad))) + \
             bytearray(b"\x1e\x1e\x1e\xff" * (w * (h - (pad + cell + pad))))

    def blend(x0, y0, size, rgba, z):
        for y in range(size):
            for x in range(size):
                i = (y * size + x) * 4
                a = rgba[i + 3]
                if a == 0:
                    continue
                af = a / 255.0
                for dy in range(z):
                    for dx in range(z):
                        px, py = x0 + x * z + dx, y0 + y * z + dy
                        if not (0 <= px < w and 0 <= py < h):
                            continue
                        j = (py * w + px) * 4
                        for c in range(3):
                            canvas[j + c] = int(rgba[i + c] * af + canvas[j + c] * (1 - af))

    x = pad
    for s in small:
        blend(x, pad, s, render(s), zoom)
        x += s * zoom + pad
    y2 = pad + cell + pad
    x = pad
    for s in dark:
        blend(x, y2, s, render(s), zoom)
        x += s * zoom + pad
    return w, h, bytes(canvas)


def write_png(path, w, h, rgba):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(_png(w, h, rgba))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--preview", help="额外导出一张 256x256 PNG，便于核对"
                                      "（可选值 'auto' = 与 --out 同目录同名）")
    ap.add_argument("--sheet", help="导出跨尺寸自查图（浅底/深底各一排）到指定 PNG")
    ap.add_argument("--sizes", help="自定义尺寸列表，逗号分隔（默认 16..256）")
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",")] if args.sizes else SIZES
    data, used = build_ico(sizes)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(data)
    print("WROTE %s  (%.1f KB; sizes=%s)"
          % (args.out, len(data) / 1024.0, ",".join(str(s) for s in used)))

    if args.preview:
        p = args.preview
        if p == "auto":
            p = os.path.splitext(args.out)[0] + "_preview.png"
        write_png(p, 256, 256, render(256))
        print("WROTE %s" % p)

    if args.sheet:
        w, h, rgba = build_sheet(sizes)
        write_png(args.sheet, w, h, rgba)
        print("WROTE %s  (%dx%d)" % (args.sheet, w, h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
