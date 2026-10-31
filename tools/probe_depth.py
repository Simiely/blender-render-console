# -*- coding: utf-8 -*-
"""实测：这个 Blender 里每种 `file_format` **实际接受**哪些 `color_depth`。

为什么需要它：`guimodel.FORMAT_SPECS` 里那张位数白名单是照这个脚本的输出填的，
**不是照官方手册**。官方手册在 Cineon（写 8/10/12/16）与 Iris（写 8/16）两处
与本机 Blender 5.2.2 的实测不符（实测只认 10 / 8）。照着文档写会得到一个
"选了 16 位、实际还是 8 位"的静默错误（赋值不抛异常、也不生效）。

用法（本脚本跑在 Blender 进程内，本机 Python 没有 bpy）：

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b -P tools/probe_depth.py

输出一行 `##PROBE##{JSON}`：每个格式的默认位数、逐个候选的赋值结果、以及**生效**的位数。
`SET_OK` 里读回值等于被赋值的才算"接受"——只记"没抛异常"会把静默忽略的也当成支持。

⚠️ 换 Blender 版本后重跑一遍，对照 `FORMAT_SPECS` 更新（见 AGENTS 坑 59）。
"""

import json
import sys

import bpy

CANDIDATES = ["8", "10", "12", "16", "32"]


def main():
    sc = bpy.context.scene
    imf = sc.render.image_settings

    formats = [it.identifier for it in
               bpy.types.ImageFormatSettings.bl_rna.properties["file_format"].enum_items]

    result = {"blender": bpy.app.version_string, "formats": {}}
    for fmt in formats:
        rec = {}
        try:
            imf.file_format = fmt
            rec["default_depth"] = imf.color_depth
            rec["default_mode"] = imf.color_mode
            accepted = []
            for d in CANDIDATES:
                try:
                    imf.color_depth = d
                    rec.setdefault("set_ok", []).append((d, imf.color_depth))
                    if imf.color_depth == d:
                        accepted.append(d)
                except Exception as e:
                    rec.setdefault("set_err", []).append((d, type(e).__name__))
            rec["valid"] = accepted
        except Exception as e:
            rec["format_err"] = "%s: %s" % (type(e).__name__, e)
        result["formats"][fmt] = rec

    sys.stdout.write("##PROBE##" + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
