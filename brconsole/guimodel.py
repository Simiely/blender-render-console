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

from .core import UNLIMITED, parse_restart_limit
from .eta import fmt_duration
from .state import JobState

KEEP = ""          # 下拉框里"保持工程设置"对应的实际值就是空

# 场景下拉的两个占位文案（都不是真实场景名，交给 scene_to_config 过滤掉）
SCENE_DEFAULT_LABEL = "（用工程默认场景）"
SCENE_NEED_READ_LABEL = "（先读取工程配置）"
SCENE_PLACEHOLDERS = (SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL)

# 「最多重启几次」下拉的选项。
#
# ⚠️ 这里**只放显示文案**，不再维护「文案 ↔ 实际值」两张表：早期版本的界面
# 用的是 `RESTART_VALUES`（裸的 `1/3/5/10/unlimited`），于是下拉里直接显示出了
# 英文 `unlimited`，而旁边那套中文文案（`RESTART_CHOICES`）从来没被任何控件用过。
# 现在文案本身就是要解析的输入，翻译交给 `core.parse_restart_limit`（它认中文）。
RESTART_OPTIONS = ["1 次", "3 次", "5 次（默认）", "10 次", "一直重启，直到全部渲完"]
DEFAULT_RESTART_OPTION = RESTART_OPTIONS[2]
UNLIMITED_RESTART_LABEL = RESTART_OPTIONS[-1]

# 「限次类」两个字段的默认值。`FormModel` 与界面共用这一份，
# 避免"3"散落在好几处、改一处忘一处。
DEFAULT_ATTEMPTS = "3"
DEFAULT_NO_PROGRESS = "3"

# 「不限」的写法。0 是 core/state 里**既有**的约定：`JobState.remaining/exhausted` 用
# `max_attempts <= 0` 表示不限，`RenderJob` 用 `max_no_progress_rounds > 0` 才启用兜底。
# 所以"不限"根本不需要给内核加新语义，填 0 即可。
UNLIMITED_LIMITS = ("0", "0")


def is_unlimited_restart(value):
    """这个重启选项是不是「一直重启」。

    **必须走 `parse_restart_limit` 而不是比对文案字符串**：下拉里存的是显示文案，
    重复一份字面量就等于把"文案"和"解析规则"绑死两处（历史上正是这么漂过一次 ——
    界面显示英文 `unlimited`，而中文文案表从来没有任何控件用过）。
    解析不了的值走 `default=None`，与「一直重启」区分开。
    """
    return parse_restart_limit(value, default=None) == UNLIMITED


def unlimited_limit_fill(restart_value):
    """选中某个重启选项时，「单帧最多尝试 / 连续无进展轮数」该**默认填**什么。

    选「一直重启，直到全部渲完」→ 返回 `("0", "0")`（0 = 不限，内核既有约定）：
    这才是用户选它的本意 —— 否则留下任意一个正数，任务都会在"其实还能接着重试"的时候
    提前结束（帧被判 exhausted 踢出队列，或触发 no_progress 直接刹车）。

    **只改默认值、不锁控件**：留一条退路 —— 万一卡在同一帧反复崩，
    把无进展轮数填回去就是兜底（那也是内核里唯一防死循环的闸门）。
    不是这个选项就返回 None = 别动用户填的值。
    """
    return UNLIMITED_LIMITS if is_unlimited_restart(restart_value) else None

ENGINES = [("保持工程设置（用 .blend 里的）", KEEP), ("Cycles", "CYCLES"),
           ("EEVEE", "BLENDER_EEVEE"), ("Workbench", "BLENDER_WORKBENCH")]
DEVICES = [("保持工程设置", KEEP), ("CPU", "CPU"), ("CUDA", "CUDA"),
           ("OptiX", "OPTIX"), ("HIP", "HIP"), ("oneAPI", "ONEAPI")]
FORMATS = [("保持工程设置", KEEP), ("PNG", "PNG"), ("JPEG", "JPEG"),
           ("OpenEXR", "OPEN_EXR"), ("TIFF", "TIFF")]


def scene_to_config(label):
    """场景下拉的显示值 → `JobConfig.scene`。

    占位文案和空值都表示"不指定，用工程里激活的那个场景"。
    """
    s = str(label or "").strip()
    if not s or s in SCENE_PLACEHOLDERS:
        return None
    return s


def fields_from_detail(detail, blend=""):
    """一个场景的配置详情 → 表单字段 dict（界面只负责把它 set 到控件上）。

    `detail` 来自 `inspect.read_blend_info()` 的 `scene_details[i]`（或顶层那份）。
    拿不准的字段**不放进结果** —— 宁可不改，也不要写一个错值进去。典型是
    Cycles 的 GPU 后端名：它来自用户偏好设置，`-b` 下常常读不到。
    """
    from .inspect import output_template_from

    if not detail:
        return {}
    out = {}
    if detail.get("frame_start") is not None:
        out["start"] = str(detail["frame_start"])
    if detail.get("frame_end") is not None:
        out["end"] = str(detail["frame_end"])
    if detail.get("frame_step"):
        out["step"] = str(detail["frame_step"])
    engine = (detail.get("engine") or "").upper()
    if engine in [v for _, v in ENGINES]:
        out["engine"] = engine
    if detail.get("samples") is not None:
        out["samples"] = str(detail["samples"])
    # 设备：只有能确定才改（GPU 后端名来自偏好设置）
    if (detail.get("cycles_device") or "").upper() == "CPU":
        out["device"] = "CPU"
    else:
        cdt = (detail.get("compute_device_type") or "").upper()
        if cdt in [v for _, v in DEVICES]:
            out["device"] = cdt
    res = detail.get("resolution") or []
    if len(res) == 2:
        out["width"] = str(res[0])
        out["height"] = str(res[1])
    if detail.get("resolution_percentage"):
        out["pct"] = str(detail["resolution_percentage"])
    fmt = (detail.get("file_format") or "").upper()
    if fmt in [v for _, v in FORMATS]:
        out["file_format"] = fmt
    tpl = output_template_from(detail.get("output_path"), blend)
    if tpl:
        out["output"] = tpl
    return out


def frames_to_text(frames):
    """帧号列表 → `1-240,300` 这样的紧凑文本（写回表单用）。

    只把**连续 3 帧以上**的折成区间：`1-240` 比列 240 个数字清楚得多。
    ⚠️ 折成区间后会受表单 `step` 影响（`parse_frames` 对区间是 `range(a, b+1, step)`），
    所以还原时必须把 step 一起置回 1 —— 见 `FormModel.from_config`。
    """
    fs = sorted({int(f) for f in (frames or [])})
    if not fs:
        return ""
    out, i = [], 0
    while i < len(fs):
        j = i
        while j + 1 < len(fs) and fs[j + 1] == fs[j] + 1:
            j += 1
        if j - i >= 2:
            out.append("%d-%d" % (fs[i], fs[j]))
        else:
            out.extend(str(f) for f in fs[i:j + 1])
        i = j + 1
    return ",".join(out)


def settle_task_action(kind, ev):
    """一轮跑完后该怎么处置待办存档 → `(action, reason)`。

    `action`：`"clear"` = 删档；`"stop"` = 撤掉自动续跑标记（断点保留）。

    判据是「**进程有没有活着把结果报出来**」，而不是「成功还是失败」：

    - 报出「渲完了」→ 删档，否则下次开机会白跑一轮（虽然会立刻发现已完成，仍是噪音）
    - 报出「被取消 / 明确失败 / 抛异常」→ 撤掉自动续跑标记，**断点原样保留**。
      这些结果都是**进程活着**时才写得出来的；反过来，被强杀、掉电时根本来不及写文件，
      标记就还是 True → 下次开机自动续跑。这正是本功能要的那条路。
    - 换句话说：**崩溃能续跑，失败要能停下来**。不给明确失败保留自动续跑，是因为那会让
      一个注定失败的任务每次开机都白跑一次（最典型：场景里没有相机），变成开机自启的头号
      噪音；而且"关机导致的失败"根本不会走到这里 —— 那时进程是被直接杀掉的。

    ⚠️ 这段逻辑特意放在 guimodel（不碰 tkinter）——它决定"开机要不要自动跑"，
    是这套机制里最不能出错的一环，必须能被单测直接钉住。
    """
    if kind == "job_done" and ev.get("ok"):
        return "clear", ""
    reason = ("你停止了任务" if ev.get("cancelled")
              else "上次运行没能跑完（%s）" % (ev.get("error") or "有帧一直失败"))
    return "stop", reason


def restart_label_for(value):
    """重启次数（存档里的整数）→ 下拉文案。

    `-1` / None（= 一直重启）映射到最后一个选项；不在已知选项里的整数
    直接写成 `"7 次"` —— `parse_restart_limit` 认得出开头数字，
    界面那边也会把这个文案补进下拉的候选里（否则 readonly 的下拉放不下它）。
    """
    if value is None:
        return RESTART_OPTIONS[-1]
    n = int(value)
    if n < 0:
        return RESTART_OPTIONS[-1]
    for label in RESTART_OPTIONS[:-1]:
        if parse_restart_limit(label, default=None) == n:
            return label
    return "%d 次" % n


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
        self.scene = kw.get("scene", SCENE_DEFAULT_LABEL)   # 场景名，或占位文案
        self.resume = kw.get("resume", True)
        self.max_restarts = kw.get("max_restarts", DEFAULT_RESTART_OPTION)
        self.max_no_progress = kw.get("max_no_progress", DEFAULT_NO_PROGRESS)
        self.max_frame_attempts = kw.get("max_frame_attempts", DEFAULT_ATTEMPTS)
        self.show_native = kw.get("show_native", False)
        # 界面上没有这两个控件（CLI 专有参数），但**必须原样透传**：
        # 从任务存档回填时如果丢掉它们，重建出来的任务会静默按默认值跑 ——
        # 「参数悄悄变了」比「报错」难查得多。
        self.extra_args = list(kw.get("extra_args") or ())
        self.restart_delay = kw.get("restart_delay", 2.0)

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

    # ---------- 存档 → 表单（开机续跑时把上次的任务填回界面）----------
    @classmethod
    def from_config(cls, cfg_dict, blender_exe=""):
        """`JobConfig.to_dict()` 的结果 → 表单字段。

        帧范围**走显式列表**并把 step 置回 1：`to_config` 里显式列表优先于
        起始/结束帧，而区间展开又要乘 step —— 两处都摆平才能做到"填回去和上次
        跑的是同一批帧"。这个往返有单测盯着。
        """
        d = dict(cfg_dict or {})
        res = d.get("resolution") or []
        return cls(
            blend=d.get("blend") or "",
            blender=blender_exe or "",
            output=d.get("output_template") or "",
            frames=frames_to_text(d.get("frames")),
            start="1", end="250", step="1",
            engine=d.get("engine") or KEEP,
            samples="" if d.get("samples") is None else str(d["samples"]),
            device=d.get("device") or KEEP,
            width=str(res[0]) if len(res) == 2 else "",
            height=str(res[1]) if len(res) == 2 else "",
            pct=("" if d.get("resolution_percentage") is None
                 else str(d["resolution_percentage"])),
            file_format=d.get("file_format") or KEEP,
            scene=d.get("scene") or SCENE_DEFAULT_LABEL,
            resume=bool(d.get("resume", True)),
            max_restarts=restart_label_for(d.get("max_restarts", 5)),
            max_no_progress=str(d.get("max_no_progress_rounds", int(DEFAULT_NO_PROGRESS))),
            max_frame_attempts=str(d.get("max_frame_attempts", int(DEFAULT_ATTEMPTS))),
            extra_args=d.get("extra_args") or (),
            restart_delay=d.get("restart_delay", 2.0),
        )

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

        attempts = opt_int(self.max_frame_attempts, "单帧最多尝试")
        if attempts is not None and attempts < 0:
            attempts = 0                       # 负数没有意义，按「不限」处理
            warns.append("单帧最多尝试填了负数，按「不限」处理")
        no_progress = opt_int(self.max_no_progress, "连续无进展上限")
        if no_progress is not None and no_progress < 0:
            errors.append("连续无进展上限不能是负数")

        if errors:
            return None, errors, warns

        cfg = JobConfig(
            blend=blend, frames=frames, output_template=output,
            engine=self.engine or None, samples=samples,
            device=self.device or None, resolution=resolution,
            resolution_percentage=pct, file_format=self.file_format or None,
            scene=scene_to_config(self.scene),
            state_path=None, log_path=None,
            max_restarts=parse_restart_limit(self.max_restarts, default=5),
            max_frame_attempts=int(DEFAULT_ATTEMPTS) if attempts is None else attempts,
            resume=bool(self.resume),
            extra_args=self.extra_args,
            restart_delay=self.restart_delay,
            max_no_progress_rounds=(int(DEFAULT_NO_PROGRESS) if no_progress is None
                                    else no_progress))
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
        elif kind == "no_progress":
            self.last_message = "连续 %s 轮没有任何帧推进，停止" % ev.get("rounds")
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
    if kind == "no_progress":
        return ("✗ 连续 %s 轮一帧都没推进（已完成 %s/%s），停止 —— 重试解决不了的问题，"
                "看看是不是工程/显存本身有问题（剩余 %s）"
                % (ev.get("rounds"), ev.get("done"), ev.get("total"), ev.get("remaining")))
    if kind == "resume":
        return "断点续跑：已完成 %s/%s 帧" % (len(ev.get("done") or []), ev.get("total"))
    if kind == "settings":
        return "实际设置：引擎 %s · %sx%s@%s%% · 采样 %s" % (
            ev.get("engine"), (ev.get("res") or [0, 0])[0], (ev.get("res") or [0, 0])[1],
            ev.get("pct"), "-" if ev.get("samples") is None else ev.get("samples"))
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
        scene = ev.get("scene")
        return "开始：%s（共 %s 帧%s）→ %s" % (
            ev.get("blend"), ev.get("total"),
            " · 场景 %s" % scene if scene else " · 用工程默认场景",
            ev.get("output"))
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
