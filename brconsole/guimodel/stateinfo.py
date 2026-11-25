# -*- coding: utf-8 -*-
"""断点文件的路径与摘要显示。

唯一变更原因是"界面怎么告诉用户断点在哪、里面有什么"。
规则本体复用 `core.state_file_for()` / `state.JobState`，这里不另写一套。
"""

import os

from ..core import state_file_for
from ..state import JobState


def guess_state_path(output_template):
    """断点文件的默认位置。

    直接走 core 的 `state_file_for` —— **不要在这里再写一遍目录拼接规则**：
    界面拿它显示"断点：已完成 120 帧 / 清空断点"，运行时用它读写断点，
    两处规则一旦漂开，界面会说"已完成 120 帧"而实际从一开始渲（不报错，只是骗人）。
    """
    return state_file_for(output_template or "")


def state_summary(state_path):
    """给界面显示「断点里已完成多少帧」。文件不存在/损坏都返回 None。"""
    if not state_path or not os.path.exists(state_path):
        return None
    st = JobState.load(state_path)
    if st is None:
        return None
    return {"done": st.done_count, "total": st.total,
            "failed": len(st.failed), "frames": list(st.frames)[:20]}
