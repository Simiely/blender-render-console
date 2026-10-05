# -*- coding: utf-8 -*-
r"""读取 .blend 里**已有的**渲染配置。

用途：选中工程后自动把表单填好（引擎 / 采样 / 设备 / 分辨率 / 帧范围 / 输出格式与路径），
省掉"照着 Blender 面板手抄一遍"。

做法：另起一个**无头 Blender** 打开工程，跑一段脚本把配置打成一行
`##BRCINFO##{JSON}` 再从 stdout 里捞出来。三条硬约束：

1. **绝不 save_mainfile** —— 只读，用户的 .blend 一个字节都不动
2. 输出必须带独特标记 —— Blender 自己会往 stdout 打一堆东西，裸 JSON 捞不准
3. 打开大工程可能要几十秒 —— 调用方必须放到后台线程 + 给足超时（`timeout` 参数）

**多场景**：一个 .blend 里可以有多个场景（每个场景各有一套引擎/采样/分辨率/帧范围/
输出路径）。所以脚本一次派出**两份**数据：
- 顶层字段 = 工程里**激活**的那个场景（= Blender 打开工程时显示的那个），保持向后兼容
- `scene_details = [{name, engine, ...}, ...]` = 每个场景各一份，界面切换场景时按它重填表单

一次读全比"切一次场景起一次 Blender"划算得多（读一次 ≈ 起一次 Blender，实测小工程约 2s）。

⚠️ 已知代价：读一次就要启动一次 Blender。所以界面是"选文件/点按钮才读"，不做定时轮询。
"""

import json
import os
import subprocess
import sys
import tempfile

from .core import normalise_output_template

MARK = "##BRCINFO##"
CREATE_NO_WINDOW = 0x08000000

# 运行在 Blender 内的读取脚本（保持与 driver.py 相同的"只读、不落盘"原则）
SCRIPT = r'''
import bpy, json, sys

MARK = "##BRCINFO##"

def emit(d):
    sys.stdout.write(MARK + json.dumps(d, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def samples_of(sc):
    try:
        if sc.render.engine == "CYCLES":
            return sc.cycles.samples
        if "eevee" in sc.render.engine.lower():
            for attr in ("taa_render_samples", "taa_samples"):
                if hasattr(sc.eevee, attr):
                    return getattr(sc.eevee, attr)
    except Exception:
        pass
    return None

def detail_of(sc):
    """一个场景的完整渲染配置。

    界面上切换场景时要拿这份重填表单 —— 引擎/采样/分辨率/帧范围/输出路径
    都是**每个场景各有一套**的，不能只换一个名字。
    """
    r = sc.render
    d = {
        "name": sc.name,
        "engine": r.engine,
        "samples": samples_of(sc),
        "resolution": [int(r.resolution_x), int(r.resolution_y)],
        "resolution_percentage": int(r.resolution_percentage),
        "frame_start": int(sc.frame_start),
        "frame_end": int(sc.frame_end),
        "frame_step": int(getattr(sc, "frame_step", 1) or 1),
        "fps": float(r.fps) / float(getattr(r, "fps_base", 1) or 1),
        "file_format": r.image_settings.file_format,
        "color_mode": r.image_settings.color_mode,
        "output_path": r.filepath,
        "use_file_extension": bool(r.use_file_extension),
        "camera": (sc.camera.name if sc.camera else None),
        "frame_current": int(sc.frame_current),
        "cycles_device": None,
        "compute_device_type": None,
    }
    if r.engine == "CYCLES":
        d["cycles_device"] = sc.cycles.device
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            d["compute_device_type"] = prefs.compute_device_type
        except Exception:
            pass
    return d

try:
    sc = bpy.context.scene
    info = detail_of(sc)                      # 顶层 = 激活场景那份（向后兼容）
    info.update({
        "ok": True,
        "blend": bpy.data.filepath,
        "scene": sc.name,
        "scenes": [s.name for s in bpy.data.scenes],
        "scene_details": [detail_of(s) for s in bpy.data.scenes],
        "blender_version": bpy.app.version_string,
    })
    emit(info)
except Exception as e:
    emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
'''


def parse_info(stdout):
    """从 Blender 的整段输出里捞出那条 ##BRCINFO## JSON。坏行返回 None。"""
    if not stdout:
        return None
    for line in reversed(str(stdout).replace("\r", "\n").split("\n")):
        if MARK in line:
            try:
                return json.loads(line.split(MARK, 1)[1].strip())
            except ValueError:
                return None
    return None


def output_template_from(output_path, blend="", default_name="frame"):
    """工程里的输出路径 → 本工具的模板（含 `####`）。

    工程里的路径通常长这样：`/tmp/` 或 `/tmp/render_` 或 `/tmp/render_####.png`。
    分别对应：目录 → `目录/frame_####`；前缀 → `前缀_####`；带占位符 → 原样。

    `//` 是 Blender 的"相对 .blend 所在目录"（**默认输出路径就是 `//`**），
    必须先展开成绝对路径再判断，否则会被下面"以 / 开头 = Windows 上不存在"那条规则误杀，
    结果是"读到的输出路径永远是工程名_####"、多个场景之间也看不出区别。
    展开规则与 core 共用一份（`normalise_output_template`），别在两处各写一遍。
    """
    p = (output_path or "").strip()
    if p.startswith("//"):
        # 没有工程可参照 → 只能当"用户没设置"（下面会退回用工程名命名）
        p = normalise_output_template(p, blend) if blend else ""
    # Blender 的默认输出路径是 `/tmp/`（Windows 上也一样），等于"用户没设置"；
    # 而 `/` 开头的路径在 Windows 上根本不存在 —— 两种情况都退回工程目录。
    if p.replace("\\", "/").rstrip("/") == "/tmp":
        p = ""
    if os.name == "nt" and p.startswith("/"):
        p = ""
    if not p:
        if blend:
            base = os.path.splitext(os.path.basename(blend))[0]
            return os.path.join(os.path.dirname(os.path.abspath(blend)), base + "_####")
        return ""
    if "####" in p or "#" in p:
        return p
    if os.path.isdir(p) or p.endswith(("\\", "/")):
        return os.path.join(p, default_name + "_####")
    root, ext = os.path.splitext(p)
    # 已经是 `xxx_` 这种前缀形式：补 `####` 即可，别再多加下划线
    if root.endswith(("_", "-", ".")):
        return root + "####" + ext
    return root + "_####" + ext


def script_path():
    """把读取脚本落到临时文件（onefile 打包后包内文件不在磁盘上，只能运行时释放）。"""
    fd, path = tempfile.mkstemp(prefix="brc-inspect-", suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(SCRIPT)
    return path


def read_blend_info(blender_exe, blend, timeout=180, keep_script=False):
    """读一个 .blend 的渲染配置。返回 dict；失败时 dict 里带 `error`。"""
    if not blend or not os.path.exists(blend):
        return {"ok": False, "error": "工程文件不存在：%s" % blend}
    if not blender_exe or not os.path.exists(blender_exe):
        return {"ok": False, "error": "blender.exe 不存在：%s" % blender_exe}

    drv = script_path()
    cmd = [blender_exe, "-b", blend, "-P", drv, "--python-exit-code", "1"]
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, timeout=timeout, **kwargs)
        out = p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "读取超时（%ss）——工程可能太大或 Blender 卡住了" % timeout}
    except OSError as e:
        return {"ok": False, "error": "启动 Blender 失败：%r" % (e,)}
    finally:
        if not keep_script:
            try:
                os.remove(drv)
            except OSError:
                pass

    info = parse_info(out)
    if info is None:
        tail = "\n".join(out.strip().splitlines()[-6:])
        return {"ok": False, "error": "没能读到工程配置，Blender 最后几行输出：\n%s" % tail}
    info["stdout_tail"] = "\n".join(out.strip().splitlines()[-3:])
    return info


def summarize(info):
    """给人看的摘要（界面日志用）。多场景时顺带报一下总数。"""
    if not info or not info.get("ok"):
        return "读取失败：%s" % (info or {}).get("error", "未知错误")
    res = info.get("resolution") or [0, 0]
    names = scene_names(info)
    scene_txt = str(info.get("scene"))
    if len(names) > 1:
        scene_txt += "（工程共 %d 个场景）" % len(names)
    return ("工程 %s · 场景 %s · 引擎 %s · %sx%s@%s%% · 采样 %s · 帧 %s-%s/%s · 输出 %s"
            % (os.path.basename(info.get("blend", "")), scene_txt,
               info.get("engine"), res[0], res[1], info.get("resolution_percentage"),
               "-" if info.get("samples") is None else info.get("samples"),
               info.get("frame_start"), info.get("frame_end"),
               info.get("frame_step"), info.get("output_path")))


def scene_names(info):
    """工程里的场景名列表（顺序与 Blender 里一致）。

    读失败时返回空列表；`scenes` 缺失（老版本脚本的结果）就退回顶层那一个名字。
    """
    if not info or not info.get("ok"):
        return []
    names = [str(n) for n in (info.get("scenes") or []) if n]
    if names:
        return names
    return [str(info["scene"])] if info.get("scene") else []


def scene_detail(info, name=None):
    """取某个场景的配置详情；`name` 为空 = 取激活场景那份。

    找不到指定场景时返回 None —— 调用方要明确提示，不能悄悄退回别的场景。
    """
    if not info or not info.get("ok"):
        return None
    want = str(name or info.get("scene") or "")
    for d in (info.get("scene_details") or []):
        if str(d.get("name")) == want:
            return d
    if want and want == str(info.get("scene") or ""):
        return info                      # 顶层本来就是激活场景那份
    if not want and not (info.get("scene_details") or []):
        return info
    return None


if __name__ == "__main__":              # 手动排错用：python -m brconsole.inspect 工程.blend
    exe = sys.argv[2] if len(sys.argv) > 2 else r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
    print(json.dumps(read_blend_info(exe, sys.argv[1]), ensure_ascii=False, indent=2))
