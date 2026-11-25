# -*- coding: utf-8 -*-
"""GUI 的**纯逻辑层**门面（不 import tkinter，便于单测，也便于换成别的界面库）。

内部按变更原因拆成：

- `constants` —— 下拉文案 ↔ 实际值、预览上限；
- `frames` —— 帧段解析、展开、计数与实时回显；
- `form` —— 表单字段/重试/格式/场景详情 → `JobConfig`；
- `events` —— core 事件文案、任务结算与善后；
- `progress` —— 进度/ETA 字段；
- `logs` —— 原生输出折叠与日志队列；
- `stateinfo` —— 断点路径与摘要。

本文件只做 re-export，保持历史 `from .guimodel import X` 全部可用。
新代码请直接从具体子模块 import，避免把门面重新养回上帝模块。
"""

import math  # noqa: F401  兼容历史上 `guimodel.math` 的访问
import os  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
from collections import deque  # noqa: F401

from ..core import (UNLIMITED, complete_output_template, expand_segment,  # noqa: F401
                    parse_restart_limit, state_file_for)
from ..eta import fmt_clock, fmt_duration  # noqa: F401
from ..state import JobState  # noqa: F401

from .constants import (  # noqa: F401
    AFTERMATH_BY_LABEL, AFTERMATH_LABELS, AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN,
    AFTERMATH_SLEEP, AFTERMATH_VALUES, DEFAULT_NO_PROGRESS, DEFAULT_RETRY,
    DEFAULT_SEGMENT_END, DEFAULT_SEGMENT_START, DEPTH_KEEP_LABEL, DEVICES,
    ENGINES, FORMAT_KEEP_LABEL, FORMAT_SPECS, FORMATS, KEEP,
    KNOWN_EXT_ALIASES, PREVIEW_MATERIALIZE_LIMIT, PREVIEW_MAX_LIST,
    PREVIEW_MAX_MERGED, PREVIEW_MAX_SEGMENTS, RETRY_CHOICES, RETRY_COUNTS,
    RETRY_UNLIMITED_LABEL, SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL,
    SCENE_PLACEHOLDERS)
from .events import (aftermath_plan, event_line, event_text, outcome_reason,  # noqa: F401
                     settle_task_action)
from .form import (FormModel, checks_from_hidden, coerce_depth,  # noqa: F401
                   default_depth_for, depth_choices, depth_value,
                   depths_for, fields_from_detail, format_ext_for,
                   format_from_label, format_label, format_spec,
                   hidden_from_checks, retry_count, retry_label_for,
                   retry_label_from_config, retry_note, retry_plan,
                   retarget_extension, scene_to_config)
from .frames import (_ap_intersection, _ap_union_count, _capped_frames,  # noqa: F401
                     _parse_segments, frame_count, frames_preview,
                     frames_to_segments, frames_to_text, new_segment,
                     normalise_segment, segment_label, segments_to_frames)
from .logs import LogModel, NativeLineFilter  # noqa: F401
from .progress import ProgressModel  # noqa: F401
from .stateinfo import guess_state_path, state_summary  # noqa: F401

__all__ = [
    # 历史兼容的导入别名
    "math", "os", "re", "time", "deque", "UNLIMITED",
    "complete_output_template", "expand_segment", "parse_restart_limit",
    "state_file_for", "fmt_clock", "fmt_duration", "JobState",
    # 常量
    "AFTERMATH_BY_LABEL", "AFTERMATH_LABELS", "AFTERMATH_NOTHING",
    "AFTERMATH_SHUTDOWN", "AFTERMATH_SLEEP", "AFTERMATH_VALUES",
    "DEFAULT_NO_PROGRESS", "DEFAULT_RETRY", "DEFAULT_SEGMENT_END",
    "DEFAULT_SEGMENT_START", "DEPTH_KEEP_LABEL", "DEVICES", "ENGINES",
    "FORMAT_KEEP_LABEL", "FORMAT_SPECS", "FORMATS", "KEEP",
    "KNOWN_EXT_ALIASES", "PREVIEW_MATERIALIZE_LIMIT", "PREVIEW_MAX_LIST",
    "PREVIEW_MAX_MERGED", "PREVIEW_MAX_SEGMENTS", "RETRY_CHOICES",
    "RETRY_COUNTS", "RETRY_UNLIMITED_LABEL", "SCENE_DEFAULT_LABEL",
    "SCENE_NEED_READ_LABEL", "SCENE_PLACEHOLDERS",
    # 帧段
    "_parse_segments", "new_segment", "normalise_segment", "segment_label",
    "segments_to_frames", "_ap_intersection", "_ap_union_count", "frame_count",
    "_capped_frames", "frames_to_segments", "frames_preview", "frames_to_text",
    # 表单
    "retry_count", "retry_label_for", "retry_note", "retry_plan",
    "retry_label_from_config", "format_label", "format_from_label",
    "depth_choices", "depth_value", "format_spec", "depths_for",
    "default_depth_for", "format_ext_for", "coerce_depth", "retarget_extension",
    "hidden_from_checks", "checks_from_hidden", "scene_to_config",
    "fields_from_detail", "FormModel",
    # 事件
    "outcome_reason", "event_text", "settle_task_action", "aftermath_plan",
    "event_line",
    # 模型
    "ProgressModel", "NativeLineFilter", "LogModel",
    # 断点
    "guess_state_path", "state_summary",
]
