# -*- coding: utf-8 -*-
"""min_gui 的纯逻辑：控件值 → JobConfig，事件 → 展示文案/进度。

单独成模块是为了能单测（min_gui.py 顶层要 tkinter 自举，不便 import）。
规则全部复用既有模块的唯一出口，不在本文件另写一份。
"""

import os

from brconsole import diskspace
from brconsole.core import JobConfig, parse_frames
from brconsole.guimodel.form import (depth_value, format_from_label, retry_plan,
                                     format_label)
from brconsole.guimodel.events import aftermath_plan, event_text
from brconsole.inspect import output_template_from
from brconsole.locate import resolve_blender


def build_config(values):
    """控件值 dict → JobConfig。返回 (cfg, error)；error 非空表示配置不合法。

    `values` 的键：blend / blender / frames_spec / start / end / engine / device /
    samples / width / height / pct / format / depth / output / retry / hidden / resume
    """
    blend = str(values.get("blend") or "").strip()
    blender = str(values.get("blender") or "").strip()
    if not blend or not os.path.exists(blend):
        return None, "先选一个存在的 .blend 工程"
    if not blender or not os.path.exists(blender):
        return None, "blender.exe 不存在：%s" % blender

    spec = str(values.get("frames_spec") or "").strip()
    if spec:
        try:
            frames = parse_frames(spec)
        except ValueError as exc:
            return None, str(exc)
        if not frames:
            return None, "帧列表没解析出任何帧"
    else:
        try:
            s, e = int(values.get("start")), int(values.get("end"))
        except (TypeError, ValueError):
            return None, "帧范围要是整数"
        if s > e:
            s, e = e, s
        frames = list(range(s, e + 1))

    def num(key):
        v = str(values.get(key) or "").strip()
        return int(v) if v.isdigit() else None

    res = None
    if num("width") and num("height"):
        res = (num("width"), num("height"))

    cfg = JobConfig(
        blend, frames,
        str(values.get("output") or "").strip() or output_template_from("", blend),
        engine=str(values.get("engine") or "").strip() or None,
        samples=num("samples"),
        device=str(values.get("device") or "").strip() or None,
        resolution=res,
        resolution_percentage=num("pct"),
        file_format=format_from_label(values.get("format")) or None,
        color_depth=depth_value(values.get("depth")) or None,
        hidden_collections=values.get("hidden") or (),
        scene=str(values.get("scene") or "").strip() or None,
        resume=bool(values.get("resume", True)),
    )
    restarts, attempts, no_progress = retry_plan(values.get("retry"))
    # 命令行界面给了三个独立框时以它们为准（0 = 不限，保持内核既有约定）
    if values.get("restarts_override") not in (None, ""):
        restarts = int(values["restarts_override"])
    if values.get("attempts_override") not in (None, ""):
        attempts = int(values["attempts_override"])
    if values.get("no_progress_override") not in (None, ""):
        no_progress = int(values["no_progress_override"])
    cfg.max_restarts = restarts
    cfg.max_frame_attempts = attempts
    cfg.max_no_progress_rounds = no_progress
    return cfg, ""


def check_disk(cfg):
    """开工前磁盘预检，返回 (level, message)。"""
    return diskspace.check(cfg)


def fill_from_detail(values, detail, blend=""):
    """把一个场景的 inspect.detail 回填进控件值 dict（就地改，返回它）。

    只写"拿得准"的字段；拿不准的不动控件（宁缺勿错）。规则复用 guimodel 的出口。
    """
    if not detail:
        return values
    if detail.get("frame_start") is not None:
        values["start"] = str(detail["frame_start"])
    if detail.get("frame_end") is not None:
        values["end"] = str(detail["frame_end"])
    engine = str(detail.get("engine") or "").upper()
    if engine == "BLENDER_EEVEE_NEXT":
        engine = "BLENDER_EEVEE"
    values["engine"] = engine
    if detail.get("samples") is not None:
        values["samples"] = str(detail["samples"])
    dev = str(detail.get("cycles_device") or "").upper()
    if dev in ("CPU", "CUDA", "OPTIX", "HIP", "ONEAPI"):
        values["device"] = dev
    res = detail.get("resolution") or []
    if len(res) == 2:
        values["width"], values["height"] = str(res[0]), str(res[1])
    if detail.get("resolution_percentage"):
        values["pct"] = str(detail["resolution_percentage"])
    fmt = format_label(detail.get("file_format"))
    values["format"] = fmt
    dep = str(detail.get("color_depth") or "").strip()
    values["depth"] = dep if dep else "随工程"
    tpl = output_template_from(detail.get("output_path"), blend)
    if tpl:
        values["output"] = tpl
    return values
