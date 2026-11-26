# -*- coding: utf-8 -*-
"""min_gui 的纯逻辑：控件值 → JobConfig，事件 → 展示文案/进度。

单独成模块是为了能单测（min_gui.py 顶层要 tkinter 自举，不便 import）。
规则全部复用既有模块的唯一出口，不在本文件另写一份。
"""

import os

from brconsole import diskspace, queue_store
from brconsole.core import JobConfig, parse_frames
from brconsole.guimodel.form import (depth_value, fields_from_detail,
                                     format_from_label, format_label, retry_plan)
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
    # 重试三值只从下拉来（`retry_plan` 是唯一出口）：次数同时管重启与单帧尝试，
    # 无进展轮数固定 3 —— 不给"单独覆盖"的入口，避免有人把防死循环闸门填成 0。
    cfg.max_restarts, cfg.max_frame_attempts, cfg.max_no_progress_rounds = \
        retry_plan(values.get("retry"))
    return cfg, ""


def check_disk(cfg):
    """开工前磁盘预检，返回 (level, message)。"""
    return diskspace.check(cfg)


def queue_status_after(kind, ev):
    """一轮任务结束 → 该项在队列里的状态（与完整界面同一套判定）。

    - 渲完（`ok`）→ done
    - 用户主动停止 → pending（保留断点，点开始可续）
    - 其它（失败/放弃）→ failed
    """
    if kind == "job_done" and ev.get("ok"):
        return queue_store.STATUS_DONE
    if ev.get("cancelled"):
        return queue_store.STATUS_PENDING
    return queue_store.STATUS_FAILED


def fill_from_detail(values, detail, blend=""):
    """把一个场景的 inspect.detail 回填进控件值 dict（就地改，返回它）。

    字段判定完全复用 `guimodel.form.fields_from_detail`（完整界面用的是同一个出口）——
    尤其设备：`cycles_device` 只有 CPU/GPU 两值，GPU 时的后端名在 `compute_device_type`
    里（CUDA/OPTIX/…）。早先这里只读 cycles_device，GPU 工程在下拉里显示空白。
    拿不准的字段仍由那个函数负责不动控件（宁缺勿错）。
    """
    fields = fields_from_detail(detail, blend)
    if "start" in fields:
        values["start"] = fields["start"]
    if "end" in fields:
        values["end"] = fields["end"]
    if "engine" in fields:
        values["engine"] = fields["engine"]
    if "samples" in fields:
        values["samples"] = fields["samples"]
    if "device" in fields:
        values["device"] = fields["device"]
    if "width" in fields:
        values["width"] = fields["width"]
    if "height" in fields:
        values["height"] = fields["height"]
    if "pct" in fields:
        values["pct"] = fields["pct"]
    if "file_format" in fields:
        values["format"] = format_label(fields["file_format"])
    if "color_depth" in fields:
        values["depth"] = fields["color_depth"]
    if "output" in fields:
        values["output"] = fields["output"]
    return values
