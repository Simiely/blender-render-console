# -*- coding: utf-8 -*-
"""进度、状态行与断点行的刷新。

由 `brconsole/gui/app.py` 按职责抽出；方法体保持原样。
"""

from .common import *  # noqa: F401,F403
from .common import _stamp  # noqa: F401


class RefreshControllerMixin(object):
    def _refresh(self):
        p = self.prog
        self.pb["value"] = p.fraction * 100
        # 状态 · ETA · 单帧 全在一行里 —— 不再另贴一个右对齐 Label
        tail = " · ".join(x for x in (p.eta_text(), p.per_frame_text()) if x)
        text = p.status_text()
        if self._queue_mode and self._queue_total:
            text = "队列 %d/%d · %s" % (self._queue_index, self._queue_total, text)
        self.lbl_status.configure(text=("%s　|　%s" % (text, tail)) if tail else text)
        self._refresh_metrics(p)

    def _refresh_metrics(self, p):
        """把用时 / 单帧 / 速度 / ETA / 预计完成换算成给人看的一行。"""
        panel = getattr(self, "log_panel", None)
        if panel is None:
            return
        if p.started_at is None:
            elapsed = "—"
        elif p.running:
            elapsed = fmt_duration(time.time() - p.started_at)
        else:
            elapsed = fmt_duration(p.elapsed or 0.0)
        per_frame = fmt_duration(p.per_frame) if p.per_frame else "—"
        speed = ("%.1f 帧/分" % (60.0 / p.per_frame)
                 if p.per_frame and p.per_frame > 0 else "—")
        eta = fmt_duration(p.eta_sec) if p.eta_sec else "—"
        finish = (fmt_clock(time.time() + p.eta_sec)
                  if (p.eta_sec and p.running) else "—")
        panel.set_metrics(elapsed, per_frame, speed, eta, finish)

    def _refresh_state_label(self):
        info = state_summary(guess_state_path(self.v_output.get()))
        if info:
            self.lbl_state.configure(text="断点：已完成 %d/%d 帧"
                                          % (info["done"], info["total"]))
        else:
            self.lbl_state.configure(text="断点：无")
