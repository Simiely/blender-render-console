# -*- coding: utf-8 -*-
"""把 core 事件折算成进度显示字段。

唯一变更原因是"状态栏/ETA/进度条怎么显示"。不碰 tkinter，可单测。
"""

import time

from ..eta import fmt_clock, fmt_duration


class ProgressModel(object):
    """把 core 的事件流折算成界面字段。**不碰任何控件**。"""

    def __init__(self):
        self.reset()

    def reset(self):
        """把模型恢复成"还没开始跑"。

        ⚠️ **同一个任务的第二次开始也必须调它**（界面的 `on_start` 会调）。
        原先 `job_start` 里手写了一份"重置清单"，只列了一部分字段，漏掉
        `cancelled / elapsed / current_frame / stalled / eta_sec / ok / last_message` ——
        于是「停止 → 再开始」之后：状态栏整轮都显示「**已取消** · 完成 3/6 · 用时 1.9s」
        （1.9s 还是上一轮的用时），而且 `status_text()` 里 `cancelled` 的判断在
        `running` 之前，一路盖住了"正在渲染帧 N"。
        现在默认值只有这一份，`job_start` 直接复用它，**加字段不会再漏**。
        """
        self.running = False
        self.finished = False
        self.total = 0
        self.done = 0
        self.failed = 0
        self.exhausted = 0
        self.current_frame = None
        self.per_frame = None
        self.eta_sec = None
        self.eta_mode = ""
        self.phase = ""
        self.restarts = 0
        self.crashes = 0
        self.elapsed = 0.0
        self.ok = False
        self.cancelled = False
        self.error = ""
        self.stalled = False
        self.last_message = ""
        #: 本任务是什么时候点开始跑的（job_start 收到时记）/ 是什么时候结束的
        #: （job_done 收到时记）—— 显示「预计 x 完成 / 已于 x 完成」用。
        #: 都是**本地墙钟**，不问 core 要（core 只报相对量 elapsed / eta_sec）。
        self.started_at = None
        self.finished_at = None

    def on_event(self, kind, ev):
        if kind == "job_start":
            self.reset()                 # 上一轮的残留一律清掉（见 reset 的说明）
            self.running = True
            self.started_at = time.time()
            self.total = ev.get("total") or 0
            # 续跑时 core 会带上**已完成帧数**：进度条要立刻落在真实位置，
            # 而不是先显 0% 再在第一帧完成时跳到 "2/6"（那样看着像断点没加载上）
            self.done = ev.get("done") or 0
            self.phase = ""
        elif kind == "frame_start":
            self.current_frame = ev.get("frame")
            self.stalled = False
        elif kind == "frame_done":
            self.current_frame = None
            self.done = ev.get("done") or self.done + 1
            self.total = ev.get("total") or self.total
            self.per_frame = ev.get("per_frame")
            self.eta_sec = ev.get("eta_sec")
            self.eta_mode = ev.get("eta_mode") or ""
            self.stalled = False
            if ev.get("warmup"):
                self.last_message = "首帧已完成（预热帧不计入 ETA 基线）"
        elif kind == "frame_error":
            self.failed += 1
            self.last_message = "帧 %s 失败：%s" % (ev.get("frame"), ev.get("err"))
        elif kind == "native":
            if ev.get("phase"):
                self.phase = ev["phase"]
                self.stalled = False
        elif kind == "stall":
            self.stalled = True
            self.phase = ev.get("phase") or self.phase
        elif kind == "crash":
            self.crashes += 1
            self.restarts = ev.get("restart_index") or self.restarts
            self.last_message = "Blender 退出码 %s，准备第 %s 次重启" % (
                ev.get("rc"), ev.get("restart_index"))
        elif kind == "restart":
            self.restarts = ev.get("restart_index") or self.restarts
        elif kind == "run_finished":
            self.last_message = "本轮子进程结束：成功 %s / 失败 %s" % (
                ev.get("ok"), ev.get("failed"))
        elif kind == "give_up":
            self.last_message = "重启次数已达上限，停止"
        elif kind == "no_progress":
            self.last_message = "连续 %s 轮没有任何帧推进，停止" % ev.get("rounds")
        elif kind == "warn":
            self.last_message = ev.get("msg", "")
        elif kind == "job_done":
            self.running = False
            self.finished = True
            self.finished_at = time.time()
            self.done = len(ev.get("done") or [])
            self.failed = len(ev.get("failed") or {})
            self.exhausted = len(ev.get("exhausted") or [])
            self.restarts = ev.get("restarts") or self.restarts
            self.elapsed = ev.get("elapsed") or 0.0
            self.ok = bool(ev.get("ok"))
            self.cancelled = bool(ev.get("cancelled"))
        elif kind == "job_error":
            self.running = False
            self.finished = True
            self.error = ev.get("error") or ""

    # ---------- 给控件用 ----------
    @property
    def fraction(self):
        """0~1；没有总数时返回 0（界面据此切 indeterminate）。"""
        if not self.total:
            return 0.0
        return max(0.0, min(1.0, self.done / float(self.total)))

    def status_text(self):
        if not self.running and not self.finished:
            return "就绪"
        # 「全部渲完」优先于「收到过取消」：最后一帧渲完的同一瞬间点停止时，
        # `cancelled` 和 `ok` 会同时为真。此时说「已取消 · 完成 6/6」是自相矛盾的
        # —— 存档那一侧已经按 ok 删了，界面不该反过来说没成。
        if self.cancelled and not (self.finished and self.ok):
            text = "已取消 · 完成 %d/%d · 用时 %s" % (
                self.done, self.total, fmt_duration(self.elapsed))
            if self.finished_at:
                text += " · 停止于 %s" % fmt_clock(self.finished_at, seconds=True)
            return text
        if self.finished:
            tail = "用时 %s" % fmt_duration(self.elapsed)
            if self.failed or self.exhausted:
                tail += " · 失败 %d · 放弃 %d" % (self.failed, self.exhausted)
            # 实际完成时刻 —— 与预估时间区分开，这才是"真的渲完"的时间点
            if self.finished_at:
                tail += " · 已于 %s 完成" % fmt_clock(self.finished_at, seconds=True)
            return ("全部完成 %d/%d · %s" if self.ok
                    else "未完成 %d/%d · %s") % (self.done, self.total, tail)
        if self.current_frame is not None:
            return "正在渲染帧 %s · 已完成 %d/%d%s" % (
                self.current_frame, self.done, self.total, self._elapsed_suffix())
        if self.stalled:
            return "%s（较久没有新输出，首帧准备/场景同步属正常）%s" % (
                self.phase or "正在渲染", self._elapsed_suffix())
        if self.phase:
            return "%s%s" % (self.phase, self._elapsed_suffix())
        return "已完成 %d/%d%s" % (self.done, self.total, self._elapsed_suffix())

    def _elapsed_suffix(self):
        """运行中那几行尾巴上的「已用 X」—— 与 ETA（还剩多久）是一对：
        一个回答"跑了多久了"，一个回答"还要多久"。
        """
        if self.started_at is None:
            return ""
        return " · 已用 %s" % fmt_duration(time.time() - self.started_at)

    def eta_text(self):
        if self.finished or not self.running:
            return ""
        if self.eta_sec is None:
            return "ETA 计算中…"
        # ETA 是"还剩多久"；下面这句把它换算成"预计几点几分完成" ——
        # 过夜挂机时，人真正想看的往往就是这个（今天只显 HH:MM，跨天带日期，见 fmt_clock）
        finish = time.time() + self.eta_sec
        return "ETA %s（%s）· 预计 %s 完成" % (
            fmt_duration(self.eta_sec), self.eta_mode or "-", fmt_clock(finish))

    def per_frame_text(self):
        return ("单帧 %s" % fmt_duration(self.per_frame)) if self.per_frame else ""

    def summary(self):
        return {"total": self.total, "done": self.done, "failed": self.failed,
                "exhausted": self.exhausted, "restarts": self.restarts,
                "crashes": self.crashes, "elapsed": self.elapsed, "ok": self.ok}
