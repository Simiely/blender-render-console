# -*- coding: utf-8 -*-
"""App 的可变状态集中声明。

这里只保存"控制器之间共享的运行状态"，不碰控件、不 import tkinter。
`App.__init__` 用 `apply_to()` 把这些字段摊平到 App 上，兼容既有控制器方法；
后续迁移应逐步改成 `self.state.<字段>`。
"""

import queue

from ..guimodel.form import FormModel
from ..guimodel.logs import LogModel, NativeLineFilter
from ..guimodel.progress import ProgressModel


class AppState(object):
    def __init__(self, demo=False, preset_blend=None, autostart_mode=False,
                 cmd_factory=None):
        self.demo = demo
        self.preset_blend = preset_blend
        self.autostart_mode = autostart_mode
        self.cmd_factory = cmd_factory

        self.form = FormModel()
        self.prog = ProgressModel()
        self.log = LogModel()
        self.native_filter = NativeLineFilter()

        self._dropped_shown = 0
        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.stopping = False

        self._inspected_path = None
        self._inspecting = False
        self._inspect_token = 0
        self._inspect_path = None
        self._inspect_item_id = None
        self._scene_info = None
        self._scene_info_cache = {}

        self._queue_refreshing = False
        self._frame_preview = []
        self._seg_rows = []
        self._queue_mode = False
        self._current_queue_item = None
        self._selected_queue_id = None
        self._last_queue_event = None
        self._queue_total = 0
        self._queue_index = 0

        self._modules = {}
        self._overview_tags = {}

    def apply_to(self, app):
        """摊平到 App，保持旧方法体可用；新增状态请先加在这里。"""
        app.__dict__.update(self.__dict__)
        return app
