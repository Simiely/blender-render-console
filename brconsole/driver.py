# -*- coding: utf-8 -*-
"""driver.py —— **运行在 Blender 进程内**的驱动脚本。

⚠️ 本文件不是给宿主 Python 直接 import 用的：它由 `core.py` 以源码字符串的形式
释放到临时目录，再经 `blender -b ... -P <driver> -- <job.json>` 加载。
这么做的唯一原因：PyInstaller `--onefile` 打包后，包内文件不在磁盘上，
直接 `-P` 指向包内路径会找不到文件（见 DEVELOPMENT.md 第四节第 5 条）。

职责：
1. 只改**内存里的 scene 设置**，绝不 `save_mainfile` —— 用户的 .blend 保持原样
2. 逐帧渲染，每帧结束 flush 一行 `##PROG##{JSON}` 结构化进度
3. 单帧异常不中断整轮：记 `frame_error` 后继续下一帧，由外层决定是否重试

坑位提示（改本文件前必读 AGENTS.md「关键坑」）：
- 每个输出行必须 `sys.stdout.flush()`，`-b` 下 stdout 非行缓冲，不 flush 会被块缓冲吞住
- Blender 自带的 `Remaining` 是帧内剩余，外层不要拿它当总 ETA
- 指定场景**必须自己激活 + 回读校验**：命令行 `-S 不存在的场景` 时 Blender 只打一行
  `Can't find scene: 'xx'` 就照常渲染默认场景，退出码还是 0（实测 5.2.2）
"""

import json
import os
import sys
import time

MARK = "##PROG##"

# 引擎别名：4.2+ 把 EEVEE 改名成 BLENDER_EEVEE_NEXT，而 5.2 的 -E 又接受 BLENDER_EEVEE。
# 这里不硬编码单一 id，改成「按运行时实际可用的 enum 项挑第一个命中」。
ENGINE_ALIASES = {
    "CYCLES": ("CYCLES",),
    "BLENDER_EEVEE": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"),
    "BLENDER_EEVEE_NEXT": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"),
    "BLENDER_WORKBENCH": ("BLENDER_WORKBENCH",),
}


def emit(kind, **kw):
    """吐一行结构化进度事件。flush 是硬性要求。"""
    kw["kind"] = kind
    kw["t"] = round(time.time(), 3)
    sys.stdout.write(MARK + json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def warn(msg):
    emit("warn", msg=msg)


def _enum_items(type_name, prop_name):
    """取某个 RNA 属性的可用 enum 项，用于规避跨版本改名。"""
    try:
        import bpy

        rna = getattr(bpy.types, type_name, None)
        if rna is None:
            return []
        prop = rna.bl_rna.properties.get(prop_name)
        if prop is None:
            return []
        return [it.identifier for it in prop.enum_items]
    except Exception:
        return []


def _set_enum(scene, type_name, prop_name, wanted, aliases, label):
    """按别名表试着设置 enum，返回实际生效的值（None=都没成功）。

    ⚠️ **不要用运行时 enum_items 做准入判断**：Cycles 是 addon，`-b` 启动时
    常不在 `RenderSettings.engine` 的 enum 列表里（实测只剩 BLENDER_EEVEE），
    照列表过滤会把一个好端端可用的引擎判成"不可用"。
    正确姿势是直接赋值 + 读回校验，失败了再退别名，全失败才警告。
    """
    for cand in aliases.get(wanted, (wanted,)):
        try:
            setattr(scene, prop_name, cand)
        except Exception:
            continue
        try:
            if getattr(scene, prop_name, None) == cand:
                return cand
        except Exception:
            pass
    items = _enum_items(type_name, prop_name)
    warn("%s '%s' 设置失败（当前可选：%s），保持工程原设置"
         % (label, wanted, ",".join(items) or "未知"))
    return None


def load_job():
    """从 `--` 之后拿到 job.json 路径并解析。"""
    argv = sys.argv
    if "--" in argv:
        rest = argv[argv.index("--") + 1:]
    else:
        rest = []
    path = rest[0] if rest else os.environ.get("BRC_JOB_JSON", "")
    if not path or not os.path.exists(path):
        raise SystemExit("driver: 找不到 job.json（argv=%s）" % (rest,))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def activate_scene(name):
    """把要渲染的场景切成当前场景，返回 (实际场景, 名字是否对得上)。

    `name` 为空 = 用户没指定，用工程里激活的那个（= Blender 打开工程时显示的场景）。

    ⚠️ 为什么不用命令行 `-S`：那条路名字写错时 Blender **只打一行
    `Can't find scene: 'xx'` 就继续渲默认场景，退出码还是 0**（实测 5.2.2）——
    用户会拿到一整套错场景的图却以为一切正常。所以自己切 + 回读校验，
    对不上就明确告警，外层还能据此把实际场景名写进日志。
    """
    import bpy

    cur = bpy.context.scene
    if not name:
        return cur, True
    target = bpy.data.scenes.get(name)
    if target is None:
        warn("工程里没有场景 '%s'（现有：%s），已改用默认场景 '%s'"
             % (name, ", ".join(s.name for s in bpy.data.scenes), cur.name))
        return cur, False
    if cur.name == name:
        return cur, True

    # ① 后台模式下 bpy.context.window 实测是有的（5.2.2），直接切
    try:
        bpy.context.window.scene = target
        if bpy.context.scene.name == name:
            return bpy.context.scene, True
    except Exception:
        pass
    # ② 退一步：从 window_manager 里挑一个能写的窗口
    for w in list(getattr(bpy.context.window_manager, "windows", []) or []):
        try:
            w.scene = target
            if bpy.context.scene.name == name:
                return bpy.context.scene, True
        except Exception:
            continue
    warn("切换场景到 '%s' 失败，仍在渲染 '%s'" % (name, bpy.context.scene.name))
    return bpy.context.scene, False


def apply_overrides(sc):
    """把命令行覆盖项写进 scene（只在内存里，不落盘）。"""
    import bpy

    job = JOB

    # ---- 引擎 ----
    # 注意目标是 `scene.render`（RenderSettings），不是 scene 本身
    engine = job.get("engine")
    if engine:
        actual = _set_enum(sc.render, "RenderSettings", "engine", engine,
                           ENGINE_ALIASES, "引擎")
        emit("engine_set", requested=engine, actual=actual or sc.render.engine)

    # ---- 渲染设备（仅 Cycles）----
    device = job.get("device")
    if device and sc.render.engine == "CYCLES":
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            dev = device.upper()
            if dev == "CPU":
                sc.cycles.device = "CPU"
            else:
                # GPU 路线：先设后端类型，再刷新设备列表并把可用设备全部勾上
                prefs.compute_device_type = dev
                try:
                    prefs.get_devices()
                except Exception:
                    pass
                used = []
                for d in getattr(prefs, "devices", []):
                    try:
                        d.use = True
                        used.append(d.name)
                    except Exception:
                        pass
                if not used:
                    warn("未找到可用的 %s 设备，退回 CPU" % dev)
                    sc.cycles.device = "CPU"
                else:
                    sc.cycles.device = "GPU"
                    emit("device_set", type=dev, devices=used[:4])
        except Exception as e:
            warn("设置渲染设备失败：%r" % (e,))

    # ---- 采样 ----
    samples = job.get("samples")
    if samples:
        try:
            if sc.render.engine == "CYCLES":
                sc.cycles.samples = int(samples)
            elif "eevee" in sc.render.engine.lower():
                # 4.2+ 用 taa_render_samples；老版本别名兜底，失败就警告
                for attr in ("taa_render_samples", "taa_samples"):
                    if hasattr(sc.eevee, attr):
                        setattr(sc.eevee, attr, int(samples))
                        break
                else:
                    warn("EEVEE 无可用采样属性，忽略 --samples")
        except Exception as e:
            warn("设置采样失败：%r" % (e,))

    # ---- 分辨率 ----
    res = job.get("resolution")
    if res:
        try:
            sc.render.resolution_x = int(res[0])
            sc.render.resolution_y = int(res[1])
        except Exception as e:
            warn("设置分辨率失败：%r" % (e,))
    if job.get("resolution_percentage"):
        try:
            sc.render.resolution_percentage = int(job["resolution_percentage"])
        except Exception as e:
            warn("设置分辨率百分比失败：%r" % (e,))

    # ---- 输出格式 ----
    fmt = job.get("file_format")
    if fmt:
        try:
            sc.render.image_settings.file_format = fmt
        except Exception as e:
            warn("设置输出格式失败：%r" % (e,))

    # ---- 输出路径 ----
    # 关掉自动扩展名，路径完全由我们决定（见 output_path_for 里的实测说明）
    sc.render.use_file_extension = False
    sc.render.filepath = output_path_for(0)
    emit("info", engine=sc.render.engine,
         res=[sc.render.resolution_x, sc.render.resolution_y],
         pct=sc.render.resolution_percentage,
         output=job["output_template"],
         first_frame_path=output_path_for(job["frames"][0] if job.get("frames") else 0),
         samples=_current_samples(sc))


# 输出格式 → 扩展名（只列常用的，未知格式就让 Blender 自己决定）
# 模板"已经带了扩展名"的判定集合 = 上面这些规范后缀 + **常见的另一种写法**。
# 只看规范后缀是不够的：用户手打 `-o out/f_####.jpeg` 或工程里存的是 `.tiff` 时，
# 判定落空 → 后面再补一次 → 写出 `f_0001.jpeg.jpg` 这种双扩展名。
# （2026-10-05 写 driver 契约测试时抓到的）
KNOWN_EXT_ALIASES = {"jpeg", "tiff"}

FORMAT_EXT = {
    "PNG": "png", "JPEG": "jpg", "JPEG2000": "jp2", "OPEN_EXR": "exr",
    "OPEN_EXR_MULTILAYER": "exr", "TIFF": "tif", "BMP": "bmp", "TARGA": "tga",
    "HDR": "hdr", "DPX": "dpx", "CINEON": "cin", "IRIS": "rgb", "WEBP": "webp",
}


def _current_samples(sc):
    """当前引擎的实际采样设置（不同引擎属性名不一样）。"""
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


def output_path_for(frame):
    """把模板变成最终落盘路径：`out/f_####` → `out/f_0001.png`。

    两条实测结论（别凭直觉改）：
    1. `bpy.ops.render.render(write_still=True)` **不替换** `####` —— 保留它会写出
       一个真叫 `f_####.png` 的文件（见 tools/probe 的对比实验）。必须自己替换。
    2. 也不能靠 `scene.render.frame_path()` 算期望路径：当 filepath 里已经没有 `#`
       时它会在文件名后**再追加一次帧号**，给出 `f_00010001.png` 这种鬼东西。
    所以：手动替换 `####` + 手动补扩展名 + `use_file_extension=False`。
    """
    base = JOB["output_template"].replace("####", "%04d" % frame)
    ext = os.path.splitext(base)[1].lstrip(".").lower()
    if ext in set(FORMAT_EXT.values()) | KNOWN_EXT_ALIASES:
        return base                      # 用户模板自带扩展名，不要再加一次
    fmt = JOB.get("file_format") or None
    if fmt:
        ext = FORMAT_EXT.get(fmt.upper())
    if not ext:
        try:
            import bpy
            fmt = bpy.context.scene.render.image_settings.file_format
            ext = FORMAT_EXT.get(fmt, fmt.lower())
        except Exception:
            ext = "png"
    return "%s.%s" % (base, ext)


def main():
    import bpy

    job = JOB
    requested = job.get("scene")
    sc, scene_ok = activate_scene(requested)
    frames = [int(f) for f in job["frames"]]

    emit("start", frames=frames, blend=bpy.data.filepath,
         scene=sc.name, scene_requested=requested, scene_ok=scene_ok,
         engine=sc.render.engine,
         frame_start=sc.frame_start, frame_end=sc.frame_end)

    apply_overrides(sc)

    total = len(frames)
    ok, failed = 0, 0
    for i, f in enumerate(frames):
        sc.frame_set(f)
        path = output_path_for(f)
        sc.render.filepath = path
        t0 = time.time()
        emit("frame_start", frame=f, index=i, total=total, path=path)
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            failed += 1
            emit("frame_error", frame=f, index=i, err=repr(e),
                 secs=round(time.time() - t0, 3))
            continue
        secs = round(time.time() - t0, 3)
        # 以磁盘上真实存在的文件为准，避免扩展名推断错误导致外层误判
        real = path if path and os.path.exists(path) else ""
        if not real:
            emit("warn", msg="帧 %d 渲染完成但找不到产物：%s" % (f, path))
        ok += 1
        emit("frame_done", frame=f, index=i, total=total, secs=secs,
             path=real, done=i + 1)

    emit("all_done", n=total, ok=ok, failed=failed)


JOB = None

if __name__ == "__main__":
    JOB = load_job()
    main()
