# -*- coding: utf-8 -*-
"""后台事件队列轮询与事件分派。

由 `brconsole/gui/app.py` 按职责抽出；方法体保持原样。
"""

import os
import queue

from .. import notify, queue_store, taskstore
from ..guimodel.events import event_line, settle_task_action
from .common import POLL_MS


class EventControllerMixin(object):
    def _poll(self):
        try:
            while True:
                kind, ev = self.q.get_nowait()
                self._handle(kind, ev)
        except queue.Empty:
            pass
        self._flush_log()
        self._refresh()
        self.root.after(POLL_MS, self._poll)

    def _handle(self, kind, ev):
        if kind == "__inspect__":        # 后台读工程配置的结果，不进 ProgressModel
            self._apply_inspect(ev)
            return
        if kind == "job_start":
            self.native_filter.reset()   # 新任务重新记账，别把上一轮的折叠数带过来
        self.prog.on_event(kind, ev)
        if kind == "native":
            if self.v_native.get() and ev.get("line"):
                # 默认开启 + 折叠：采样进度一秒最多留一条，被折掉的数量会附在下一条前面
                for text in self.native_filter.feed(ev["line"]):
                    self.log.add("  | %s" % text)
            elif ev.get("phase"):
                self.log.add("  · %s" % ev["phase"])
            return
        line = event_line(kind, ev)
        if line:
            self.log.add(line)
        if kind in ("job_done", "job_error"):
            # 任务收尾：把最后一段的折叠数交代清楚，否则尾部那截等于被静默吞掉
            for text in self.native_filter.flush():
                self.log.add("  | %s" % text)
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.btn_clear_state.configure(state="normal")
            self.worker = None
            self._refresh_state_label()
            self._settle_task(kind, ev)
            self._finish_notify(kind, ev)
            # 「开始渲染」永远是队列模式：标记当前项后自动进下一项；
            # 关机/睡眠只在**整个队列**结束时判，不在中间项触发。
            # 例外：用户主动停止 → 停整个队列，当前项保留断点，点开始可续跑。
            item = getattr(self, "_current_queue_item", None)
            cancelled = bool(ev.get("cancelled"))
            if item:
                status = (queue_store.STATUS_DONE if kind == "job_done" and ev.get("ok")
                          else queue_store.STATUS_PENDING if cancelled
                          else queue_store.STATUS_FAILED)
                queue_store.set_status(item["id"], status)
                self._log("队列项已标记：%s（%s）" % (
                    status, os.path.basename(item["config"]["blend"])))
            self._last_queue_event = (kind, ev)
            self._refresh_queue()
            if cancelled:
                self._queue_mode = False
                self._log("队列已暂停（当前项保留断点；点「开始渲染」可接着跑）")
            elif queue_store.next_pending() is None:
                self._finish_queue()
            else:
                self.root.after(300, self._run_next_queue_item)

    def _settle_task(self, kind, ev):
        """一轮跑完后处置待办存档。判据在 `guimodel.settle_task_action`（可单测）。"""
        action, reason = settle_task_action(kind, ev)
        if action == "clear":
            if taskstore.clear():
                self._log("全部帧渲完，已清理待办存档（下次开机不会再自动跑）")
        elif taskstore.set_autoresume(False, reason):
            self._log("已撤掉开机自动续跑（原因：%s；断点保留，修好后点「开始渲染」接着跑）"
                      % reason)
        self._sync_resume_ui()

    def _finish_notify(self, kind, ev):
        """渲完/出错时发通知（提示音 + 任务栏闪烁 + toast 尽力而为）。

        过夜渲染人不在屏幕前，结果必须能穿过注意力盲区 —— 竞品 BRQ 的卖点
        "排队→挂机→走人→收通知"。失败也通知：早上应该知道"没渲完"而不是白等。
        demo 自检旁路：假任务不值得打扰（与 aftermath 的旁路对称）。
        """
        if self.demo:
            return
        if kind == "job_done":
            # `ok` 优先：「最后一帧渲完的同一瞬间点了停止」时 ok 和 cancelled 同时为真，
            # 这时说「渲染已取消」与「存档已按完成删掉」互相打脸（见 P-W3）
            if ev.get("ok"):
                title = "渲染完成"
            else:
                title = "渲染已取消" if ev.get("cancelled") else "渲染未完成"
        else:
            title = "渲染出错"
        # 结果文案直接复用进度模型的口径，别再拼一份
        msg = self.prog.status_text()
        r = notify.announce(self.root.winfo_id(), title, msg)
        self._log("%s（通知：提示音 %s · 任务栏闪烁 %s · toast %s）"
                  % (title, "✓" if r.get("beep") else "×",
                     "✓" if r.get("flash") else "×",
                     "✓" if r.get("toast") else "×"))
