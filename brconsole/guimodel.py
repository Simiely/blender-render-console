# -*- coding: utf-8 -*-
"""GUI 的**纯逻辑层**（不 import tkinter，便于单测，也便于换成别的界面库）。

分三块：
- `FormModel`  —— 表单字段 → `JobConfig`，含所有校验与"补全"规则
- `ProgressModel` —— 把 core 的事件流折算成界面要显示的几个字段
- `LogModel`   —— 日志面板的环形缓冲（防止长任务把内存吃满）

界面层（`gui.py`）只负责把这三个模型贴到控件上，业务逻辑一行都不写。
"""

import os
from collections import deque

from .eta import fmt_duration
from .state import JobState

KEEP = ""          # 下拉框里"保持工程设置"对应的实际值就是空
ENGINES = [("保持工程设置（用 .blend 里的）", KEEP), ("Cycles", "CYCLES"),
           ("EEVEE", "BLENDER_EEVEE"), ("Workbench", "BLENDER_WORKBENCH")]
DEVICES = [("保持工程设置", KEEP), ("CPU", "CPU"), ("CUDA", "CUDA"),
           ("OptiX", "OPTIX"), ("HIP", "HIP"), ("oneAPI", "ONEAPI")]
FORMATS = [("保持工程设置", KEEP), ("PNG", "PNG"), ("JPEG", "JPEG"),
           ("OpenEXR", "OPEN_EXR"), ("TIFF", "TIFF")]


class FormModel(object):
    """界面表单的字段容器。`to_config()` 负责校验与补全。"""

    def __init__(self, **kw):
        self.blend = kw.get("blend", "")
        self.blender = kw.get("blender", "")
        self.output = kw.get("output", "")
        self.frames = kw.get("frames", "")          # 显式帧列表，可空
        self.start = kw.get("start", "1")
        self.end = kw.get("end", "250")
        self.step = kw.get("step", "1")
        self.engine = kw.get("engine", KEEP)
        self.samples = kw.get("samples", "")
        self.device = kw.get("device", KEEP)
        self.width = kw.get("width", "")
        self.height = kw.get("height", "")
        self.pct = kw.get("pct", "")
        self.file_format = kw.get("file_format", KEEP)
        self.resume = kw.get("resume", True)
        self.max_restarts = kw.get("max_restarts", "5")
        self.max_frame_attempts = kw.get("max_frame_attempts", "3")
        self.show_native = kw.get("show_native", False)

    # ---------- 补全 ----------
    @staticmethod
    def complete_output(path, blend=""):
        """用户给目录/文件名时自动补出 `name_####.ext` 形式的模板。"""
        if not path:
            if blend:
                base = os.path.splitext(os.path.basename(blend))[0]
                return os.path.join(os.path.dirname(os.path.abspath(blend)), base + "_####")
            return ""
        if "####" in path:
            return path
        base, ext = os.path.splitext(path)
        # 没扩展名就当目录（输出模板一般带 .png/.exr，不带扩展名的多半是目录）
        if (not ext) or os.path.isdir(path) or path.endswith(("\\", "/")):
            return os.path.join(path, "frame_####")
        return base + "_####" + ext

    @staticmethod
    def default_blend_dir(blend):
        return os.path.dirname(os.path.abspath(blend)) if blend else ""

    # ---------- 校验 → JobConfig ----------
    def to_config(self, frames_parser):
        """返回 (JobConfig|None, errors:list[str], warnings:list[str])。

        `frames_parser` 传入 cli.parse_frames（界面不自己实现一遍帧解析）。
        """
        from .core import JobConfig

        errors, warns = [], []

        blend = (self.blend or "").strip()
        if not blend:
            errors.append("还没选 .blend 工程文件")
        elif not os.path.exists(blend):
            errors.append("工程文件不存在：%s" % blend)
        elif not blend.lower().endswith(".blend"):
            warns.append("这个文件的扩展名不是 .blend，确认没选错？")

        frames = []
        try:
            start = int(str(self.start).strip()) if str(self.start).strip() else None
            end = int(str(self.end).strip()) if str(self.end).strip() else None
            step = int(str(self.step).strip() or 1) or 1
            frames = frames_parser((self.frames or "").strip(), start, end, step)
        except ValueError as e:
            errors.append("帧范围解析失败：%s" % e)
        if not frames:
            errors.append("没有可渲染的帧（检查起始/结束帧或帧列表）")

        output = self.complete_output(self.output.strip(), blend)
        if not output:
            errors.append("还没填输出模板")

        def opt_int(v, name):
            v = str(v).strip()
            if not v:
                return None
            try:
                return int(v)
            except ValueError:
                errors.append("%s 必须是整数，当前：%s" % (name, v))
                return None

        samples = opt_int(self.samples, "采样")
        pct = opt_int(self.pct, "分辨率百分比")
        width = opt_int(self.width, "宽度")
        height = opt_int(self.height, "高度")
        resolution = (width, height) if (width and height) else None
        if bool(width) != bool(height):
            errors.append("分辨率要填就宽高都填")

        for label, v in (("最多重启次数", self.max_restarts),
                         ("单帧最多尝试", self.max_frame_attempts)):
            n = opt_int(v, label)
            if n is not None and n < 0:
                errors.append("%s 不能是负数" % label)

        if errors:
            return None, errors, warns

        cfg = JobConfig(
            blend=blend, frames=frames, output_template=output,
            engine=self.engine or None, samples=samples,
            device=self.device or None, resolution=resolution,
            resolution_percentage=pct, file_format=self.file_format or None,
            state_path=None, log_path=None,
            max_restarts=int(self.max_restarts or 5),
            max_frame_attempts=int(self.max_frame_attempts or 3),
            resume=bool(self.resume))
        return cfg, errors, warns

    def as_dict(self):
        return dict(self.__dict__)


class ProgressModel(object):
    """把 core 的事件流折算成界面字段。**不碰任何控件**。"""

    def __init__(self):
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

    def on_event(self, kind, ev):
        if kind == "job_start":
            self.running = True
            self.finished = False
            self.total = ev.get("total") or 0
            self.done = 0
            self.failed = 0
            self.exhausted = 0
            self.restarts = 0
            self.crashes = 0
            self.error = ""
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
        elif kind == "warn":
            self.last_message = ev.get("msg", "")
        elif kind == "job_done":
            self.running = False
            self.finished = True
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
        if self.cancelled:
            return "已取消 · 完成 %d/%d · 用时 %s" % (
                self.done, self.total, fmt_duration(self.elapsed))
        if self.finished:
            tail = "用时 %s" % fmt_duration(self.elapsed)
            if self.failed or self.exhausted:
                tail += " · 失败 %d · 放弃 %d" % (self.failed, self.exhausted)
            return ("全部完成 %d/%d · %s" if self.ok
                    else "未完成 %d/%d · %s") % (self.done, self.total, tail)
        if self.current_frame is not None:
            return "正在渲染帧 %s · 已完成 %d/%d" % (
                self.current_frame, self.done, self.total)
        if self.stalled:
            return "%s（已有一会儿没有新输出，属于正常现象）" % (self.phase or "正在渲染")
        if self.phase:
            return self.phase
        return "已完成 %d/%d" % (self.done, self.total)

    def eta_text(self):
        if self.finished or not self.running:
            return ""
        if self.eta_sec is None:
            return "ETA 计算中…"
        return "ETA %s（%s）" % (fmt_duration(self.eta_sec), self.eta_mode or "-")

    def per_frame_text(self):
        return ("单帧 %s" % fmt_duration(self.per_frame)) if self.per_frame else ""

    def summary(self):
        return {"total": self.total, "done": self.done, "failed": self.failed,
                "exhausted": self.exhausted, "restarts": self.restarts,
                "crashes": self.crashes, "elapsed": self.elapsed, "ok": self.ok}


class LogModel(object):
    """日志面板的**待消费队列**。

    长任务会吐几万行，所以模型只攒"还没被界面取走的行"（界面按帧率批量取），
    行数上限交给界面控件自己裁剪 —— 模型不替界面保存历史。
    """

    def __init__(self, capacity=4000):
        self.capacity = capacity
        self._pending = deque()
        self.total = 0                 # 累计收到的行数（界面对账用）

    def add(self, text):
        for line in str(text).replace("\r", "\n").split("\n"):
            if line.strip():
                self._pending.append(line.rstrip())
                self.total += 1

    def drain(self, limit=500):
        """取走待显示的行（最多 limit 条，避免一次卡死界面）。"""
        out = []
        while self._pending and len(out) < limit:
            out.append(self._pending.popleft())
        return out

    def clear(self):
        self._pending.clear()

    def __len__(self):
        return len(self._pending)


def event_line(kind, ev):
    """把 core 事件翻成一行面板日志；返回 None 表示这条不用显示。"""
    if kind == "native":
        return None                                  # 原生行由界面按开关单独处理
    if kind == "frame_done":
        tail = ""
        if ev.get("eta_sec") is not None:
            tail = "，剩余 %d 帧，ETA %s" % (ev.get("remaining") or 0,
                                             fmt_duration(ev.get("eta_sec")))
        return "✓ 帧 %s 完成 %s（%s/%s%s）%s" % (
            ev.get("frame"), fmt_duration(ev.get("secs")),
            ev.get("done"), ev.get("total"),
            " · 预热帧不计入基线" if ev.get("warmup") else "", tail)
    if kind == "frame_error":
        return "✗ 帧 %s 失败：%s" % (ev.get("frame"), ev.get("err"))
    if kind == "crash":
        return "⚠ Blender 退出（码 %s），已完成 %s/%s，准备第 %s 次重启" % (
            ev.get("rc"), ev.get("done"), ev.get("total"), ev.get("restart_index"))
    if kind == "restart":
        return "↻ 第 %s 次重启，剩余帧 %s" % (ev.get("restart_index"), ev.get("pending"))
    if kind == "run_start":
        return "启动 Blender（第 %s 轮，待渲染 %s 帧）" % (
            ev.get("restart_index"), len(ev.get("pending") or []))
    if kind == "stall":
        return "… 已 %ss 没有新输出（%s），大场景同步属于正常现象" % (
            ev.get("idle_sec"), ev.get("phase"))
    if kind == "warn":
        return "! %s" % ev.get("msg", "")
    if kind == "give_up":
        return "✗ 重启次数已达上限，停止（剩余 %s）" % ev.get("remaining")
    if kind == "resume":
        return "断点续跑：已完成 %s/%s 帧" % (len(ev.get("done") or []), ev.get("total"))
    if kind == "settings":
        return "实际设置：引擎 %s · %sx%s@%s%% · 采样 %s" % (
            ev.get("engine"), (ev.get("res") or [0, 0])[0], (ev.get("res") or [0, 0])[1],
            ev.get("pct"), ev.get("samples"))
    if kind == "device_set":
        return "渲染设备：%s（%s）" % (ev.get("type"), ", ".join(ev.get("devices") or []))
    if kind == "engine_set" and ev.get("requested") != ev.get("actual"):
        return "引擎 %s → 实际 %s" % (ev.get("requested"), ev.get("actual"))
    if kind == "job_done":
        return "任务结束：完成 %s/%s · 失败 %s · 放弃 %s · 重启 %s 次 · 用时 %s" % (
            len(ev.get("done") or []), ev.get("total"), len(ev.get("failed") or {}),
            len(ev.get("exhausted") or []), ev.get("restarts"),
            fmt_duration(ev.get("elapsed")))
    if kind == "job_error":
        return "✗ %s" % ev.get("error")
    if kind == "job_start":
        return "开始：%s（共 %s 帧）→ %s" % (
            ev.get("blend"), ev.get("total"), ev.get("output"))
    return None


def guess_state_path(output_template):
    """断点文件的默认位置（与 core.JobConfig 的默认规则保持一致）。"""
    d = os.path.dirname(os.path.abspath(output_template or "")) or "."
    return os.path.join(d, ".render_state.json")


def state_summary(state_path):
    """给界面显示「断点里已完成多少帧」。文件不存在/损坏都返回 None。"""
    if not state_path or not os.path.exists(state_path):
        return None
    st = JobState.load(state_path)
    if st is None:
        return None
    return {"done": st.done_count, "total": st.total,
            "failed": len(st.failed), "frames": list(st.frames)[:20]}
