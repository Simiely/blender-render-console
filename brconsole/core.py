# -*- coding: utf-8 -*-
"""调度核心：起 Blender 子进程、双通道读进度、算 ETA、崩了接着渲染。

设计要点（详见 DEVELOPMENT.md 第二节）：

- **双通道进度**：driver 的 `##PROG##{JSON}` 行给出每帧精确耗时与成败；
  Blender 原生行只作兜底，用来显示"正在编译着色器 / 构建 BVH"这类没有百分比的阶段。
- **ETA 自算**：不用 Blender 的 `Remaining`（5.2 里它是帧内剩余）。
- **崩溃续跑**：外层守护循环；每收到一帧完成就原子落盘 state，重启后只渲染剩余帧。
- **取消**：Windows 下 `taskkill /F /T`，否则 Blender 的子进程树会漏。
"""

import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .layout import FROZEN_SUBDIR
from .eta import EtaEstimator
from .parser import NativeParser
from .state import JobState

MARK = "##PROG##"
_SENTINEL = object()
CREATE_NO_WINDOW = 0x08000000          # Windows：起子进程不弹黑窗
DEFAULT_DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "driver.py")
# 打包后 .py 模块在 PYZ 归档里、磁盘上没有，driver 源码由打包脚本另存到这里（见 read_driver_source）
FROZEN_PY_SUBDIR = (FROZEN_SUBDIR, "py")

# 静默多久之后提示一次「还在干活」——大场景同步阶段可能长达数分钟
DEFAULT_STALL_AFTER = 60.0


UNLIMITED = -1          # “一直重启，直到所有帧渲染完”

# 「一直重启」的各种写法。界面的下拉直接给中文文案（"一直重启，直到全部渲完"），
# 所以这里必须用**包含**判断，不能像早期那样精确匹配几个英文单词。
UNLIMITED_WORDS = ("unlimited", "infinite", "inf", "none", "无限", "一直")


def parse_restart_limit(value, default=5):
    """把界面/命令行的输入翻译成重启次数上限。

    接受三类写法：
    - 数字，可带单位后缀：`'5'`、`'5 次（默认）'`、`'10 次'` → 对应整数
    - 「一直重启」：`'unlimited'` / `'无限'` / `'一直重启，直到全部渲完'` → `UNLIMITED`（-1）
    - 负数 `'-1'` → `UNLIMITED`（历史写法，保持兼容）

    空值、看不懂的字符串走 `default`（这一点很关键：界面下拉里存的是**显示文案**，
    不认中文就会静默退回默认值 —— 用户选了"1 次"实际按 5 次跑，是最难查的那种 bug）。
    """
    if value is None:
        return default
    s = str(value).strip().lower()
    if not s:
        return default
    if any(w in s for w in UNLIMITED_WORDS):
        return UNLIMITED
    if s.startswith("-"):
        return UNLIMITED
    m = re.match(r"\+?(\d+)", s)
    if m:
        return int(m.group(1))
    return default


def parse_frames(spec, start=None, end=None, step=1):
    """`1-10,15,20-25` / `1:240` → 帧号列表。

    语义：**显式帧列表优先**，给了它就忽略 start/end（更符合直觉，
    也避免"填了列表又带着默认范围"导致范围被悄悄并进来）。
    """
    frames = []
    spec = (spec or "").strip()
    if spec:
        for part in spec.replace(":", "-").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part.lstrip("-"):
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                if b < a:
                    a, b = b, a
                frames.extend(range(a, b + 1, step))
            else:
                frames.append(int(part))
    elif start is not None or end is not None:
        s = int(start) if start is not None else 1
        e = int(end) if end is not None else s
        if e < s:
            s, e = e, s
        frames = frames + list(range(s, e + 1, step))
    seen, out = set(), []
    for f in frames:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return sorted(out)


def normalise_output_template(template, blend=""):
    """输出模板 → 绝对路径（`//` 前缀按工程目录展开）。

    **为什么必须绝对化**：`blend` 一直就绝对化了，输出模板却原样留着 —— 可真正拿它写盘的
    是 **Blender 子进程**，它还决定断点文件放哪个目录。子进程用的是"当时的工作目录"，
    而开机自启那条路上工作目录**和当初点「开始渲染」时不是同一个**（Windows 的 Run 项
    只给一条命令行，没有指定工作目录的机制 —— Microsoft KB 179365），于是相对模板会把
    产物和断点甩到别处，开机续跑还会因为找不到断点而把已渲的帧**重渲一遍**。

    **`//` 不能直接 abspath**：那是 Blender 的"相对 .blend 所在目录"写法，而实测
    `abspath("//out/f_####")` 得到 `\\\\out\\f_####`（UNC 路径），完全不是本意。
    所以先按工程目录展开、再绝对化 —— 与 `inspect.output_template_from` 同一套规则。
    """
    tpl = (template or "").strip()
    if not tpl:
        return tpl
    if tpl.startswith("//"):
        if not blend:
            return tpl                      # 没有工程可参照，原样交给 Blender 自己解析
        rel = tpl[2:].replace("\\", "/").lstrip("/")
        return os.path.join(os.path.dirname(os.path.abspath(blend)), rel)
    return os.path.abspath(tpl)


# 存档时要落盘的全部任务参数。**加新参数时记得往这里补**，
# 漏了的话开机续跑重建出来的任务会缺这一项、静默按默认值跑（最难查的那种 bug）。
JOB_CONFIG_FIELDS = (
    "blend", "frames", "output_template", "engine", "samples", "device", "scene",
    "resolution", "resolution_percentage", "file_format", "state_path", "log_path",
    "max_restarts", "max_frame_attempts", "extra_args", "resume", "restart_delay",
    "max_no_progress_rounds",
)


class JobConfig(object):
    """一次渲染任务的全部参数。对应 CLI / GUI 上的表单。"""

    def __init__(self, blend, frames, output_template, engine=None, samples=None,
                 device=None, resolution=None, resolution_percentage=None,
                 file_format=None, state_path=None, log_path=None,
                 max_restarts=5, max_frame_attempts=3, extra_args=(),
                 resume=True, restart_delay=2.0, max_no_progress_rounds=3, scene=None):
        self.blend = os.path.abspath(blend)
        self.frames = [int(f) for f in frames]
        # 绝对化：见 normalise_output_template 的说明（开机自启时工作目录会变）。
        # 传**原始** blend：空 = 没有工程可参照，`//` 就原样留着别乱猜。
        self.output_template = normalise_output_template(output_template, blend)
        self.engine = engine
        self.samples = samples
        self.device = device
        # 场景名；None/空 = 用工程里激活的那个（= Blender 打开工程时默认显示的场景）
        self.scene = (scene or "").strip() or None
        self.resolution = tuple(resolution) if resolution else None
        self.resolution_percentage = resolution_percentage
        self.file_format = file_format
        self.state_path = state_path
        self.log_path = log_path
        self.max_restarts = max_restarts
        self.max_frame_attempts = max_frame_attempts
        self.extra_args = list(extra_args or ())
        self.resume = resume
        self.restart_delay = restart_delay
        # 「一直重启」的护栏：连续这么多轮一帧都没推进就停 —— 没有它就是真死循环
        self.max_no_progress_rounds = max_no_progress_rounds

    @property
    def unlimited_restarts(self):
        return self.max_restarts is None or self.max_restarts < 0

    def signature(self):
        """判断新旧 state 是否属于同一任务（blend / 输出 / 帧范围 / 场景变了就别续）。"""
        return (os.path.abspath(self.blend), self.output_template,
                tuple(self.frames), self.scene or "")

    def state_file(self):
        """断点文件的最终路径。

        这条规则（`state_path` 优先，否则输出目录下的 `.render_state.json`）原先只写在
        `RenderJob._init_state` 里，taskstore 也要用它判断「任务跑完没有」——
        所以在 JobConfig 上收一处，别在两处各写一遍。
        """
        return self.state_path or os.path.join(
            os.path.dirname(os.path.abspath(self.output_template)) or ".",
            ".render_state.json")

    # ---------- 存档 ----------
    # ⚠️ `blender_exe` **不在这里** —— 它是 RenderJob 的构造参数，不属于 JobConfig。
    #    开机续跑要重建整个 RenderJob，所以 taskstore 会把它单独存一份。
    def to_dict(self):
        """把任务参数整份序列化（供 taskstore 存档 / 开机续跑重建）。"""
        d = {}
        for k in JOB_CONFIG_FIELDS:
            v = getattr(self, k, None)
            d[k] = list(v) if isinstance(v, tuple) else v
        return d

    @classmethod
    def from_dict(cls, d):
        """从存档重建。**容忍缺字段**（老版本写的存档要能读），脏数据交给调用方兜。

        `frames` 为空时 `__init__` 里那句 `int(f)` 不会报错，但空任务没意义，
        所以这里不额外校验 —— 校验是 taskstore 的事（它才知道"能不能跑"）。
        """
        d = d or {}
        res = d.get("resolution")
        return cls(
            blend=d.get("blend") or "",
            frames=d.get("frames") or [],
            output_template=d.get("output_template") or "",
            engine=d.get("engine"),
            samples=d.get("samples"),
            device=d.get("device"),
            resolution=tuple(res) if res else None,
            resolution_percentage=d.get("resolution_percentage"),
            file_format=d.get("file_format"),
            state_path=d.get("state_path"),
            log_path=d.get("log_path"),
            max_restarts=d.get("max_restarts", 5),
            max_frame_attempts=d.get("max_frame_attempts", 3),
            extra_args=d.get("extra_args") or (),
            resume=d.get("resume", True),
            restart_delay=d.get("restart_delay", 2.0),
            max_no_progress_rounds=d.get("max_no_progress_rounds", 3),
            scene=d.get("scene"))


def default_cmd_factory(blender_exe, blend, driver_path, job_json, extra_args=()):
    """默认的 Blender 命令行。

    `--python-exit-code 1` 是硬要求：不加的话 Blender 会吞掉脚本异常并返回 0，
    外层会误判渲染成功（AGENTS.md 关键坑 5）。
    """
    return ([blender_exe, "-b", blend, "-P", driver_path, "--python-exit-code", "1"]
            + list(extra_args)
            + ["--", job_json])


def read_driver_source(path=None):
    """读 driver 源码字符串 —— onefile 打包后包内文件不在磁盘上，必须靠注入。

    ⚠️ 打包版**不能**直接读 `brconsole/driver.py`：PyInstaller 把 .py 模块收进 PYZ 归档，
    `_MEIPASS/brconsole/` 下根本没有这个文件（只有数据文件才落到磁盘上）。
    所以打包脚本会把 driver.py 另存一份到 `_brc/py/driver.py`，这里优先找它。
    """
    p = path or DEFAULT_DRIVER
    if not os.path.exists(p):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            cand = os.path.join(base, *FROZEN_PY_SUBDIR, "driver.py")
            if os.path.exists(cand):
                p = cand
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise RuntimeError(
            "读不到 driver 源码：%s（%s）\n"
            "打包版请检查 tools/build_exe.py 有没有把 brconsole/driver.py 当数据文件带进 _brc/py/"
            % (p, e))


class RenderJob(object):
    """一个可运行、可取消、会自己续跑的渲染任务。"""

    def __init__(self, cfg, blender_exe, driver_path=None, cmd_factory=None,
                 workdir=None, keep_workdir=False):
        self.cfg = cfg
        self.blender_exe = blender_exe
        self.driver_path = driver_path or DEFAULT_DRIVER
        self.cmd_factory = cmd_factory or default_cmd_factory
        self.workdir = workdir or tempfile.mkdtemp(prefix="brc-job-")
        self._owns_workdir = workdir is None
        self._keep_workdir = keep_workdir

        self.state = None
        self.eta = EtaEstimator()
        self.parser = NativeParser()
        self._cancel = threading.Event()
        self._proc = None
        self._log_fp = None
        self._stall_notified = False
        self._last_line_at = 0.0
        self._fed_first_frame = True      # 每个子进程的第一帧算预热
        self.restarts = 0

    # ---------------- 对外 ----------------
    def cancel(self):
        """请求停止：先标记，再杀进程树。"""
        self._cancel.set()
        self._terminate()

    def _terminate(self):
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        if os.name == "nt":
            # Blender 可能拉起子进程，只 terminate 主进程会漏
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10)
            except Exception:
                pass

    # ---------------- state ----------------
    def _init_state(self, emit):
        cfg = self.cfg
        state_path = cfg.state_file()
        os.makedirs(os.path.dirname(os.path.abspath(state_path)) or ".", exist_ok=True)

        old = JobState.load(state_path) if (cfg.resume and os.path.exists(state_path)) else None
        if old is not None:
            # 场景也要比：同一个工程换个场景，帧号往往一样但内容完全是另一张图，
            # 续跑会把上一个场景的进度算进来（产物全错还看不出来）
            same = (os.path.abspath(old.blend) == os.path.abspath(cfg.blend)
                    and old.output_template == cfg.output_template
                    and tuple(old.frames) == tuple(cfg.frames)
                    and (old.scene or "") == (cfg.scene or ""))
            if same:
                self.state = old
                if old.done:
                    emit("resume", state_path=state_path, done=sorted(old.done),
                         total=old.total)
            else:
                # 任务变了就别续 —— 但旧 state 也不删，改名留档
                bak = state_path + ".bak"
                try:
                    shutil.copyfile(state_path, bak)
                except OSError:
                    bak = "(备份失败)"
                emit("warn", msg="旧断点与当前任务不匹配，已另存 %s 并从头开始" % bak)
                self.state = None

        if self.state is None:
            self.state = JobState(
                state_path, blend=cfg.blend, frames=cfg.frames,
                output_template=cfg.output_template, engine=cfg.engine,
                samples=cfg.samples, device=cfg.device, resolution=cfg.resolution,
                file_format=cfg.file_format, scene=cfg.scene)
            self.state.save()
        return state_path

    # ---------------- 读取线程 ----------------
    def _pump(self, proc, out):
        """把 stdout 按 \\r / \\n 切成行推进队列。

        Blender 的采样进度是靠 `\\r` 原地刷新的，只按 `\\n` 切会让整轮进度挤成一行。
        这里用 raw 分块读（bufsize=0），既不丢实时性也不逐字节慢跑。
        """
        pending = b""
        try:
            while True:
                chunk = proc.stdout.read(256)
                if not chunk:
                    break
                pending += chunk
                while True:
                    i = min([p for p in (pending.find(b"\r"), pending.find(b"\n")) if p >= 0]
                            or [-1])
                    if i < 0:
                        break
                    line = pending[:i]
                    pending = pending[i + 1:]
                    if line:
                        out.put(line.decode("utf-8", "replace"))
                # 防止超长无换行行把内存吃满
                if len(pending) > 1 << 20:
                    out.put(pending.decode("utf-8", "replace"))
                    pending = b""
        except Exception as e:
            out.put("[读取失败] %r" % (e,))
        finally:
            if pending:
                out.put(pending.decode("utf-8", "replace"))
            out.put(_SENTINEL)

    # ---------------- 事件处理 ----------------
    def _handle_json(self, ev, emit):
        kind = ev.get("kind")
        state = self.state
        if kind == "start":
            emit("engine_info", blend=ev.get("blend"), engine=ev.get("engine"),
                 scene=ev.get("scene"), frames=ev.get("frames"),
                 scene_requested=ev.get("scene_requested"))
        elif kind == "engine_set":
            emit("engine_set", requested=ev.get("requested"), actual=ev.get("actual"))
        elif kind == "device_set":
            emit("device_set", type=ev.get("type"), devices=ev.get("devices"))
        elif kind == "info":
            emit("settings", engine=ev.get("engine"), res=ev.get("res"),
                 pct=ev.get("pct"), output=ev.get("output"), samples=ev.get("samples"))
        elif kind == "warn":
            emit("warn", msg=ev.get("msg", ""))
        elif kind == "frame_start":
            # 尝试次数必须**在真正开始渲染这一帧时**才 +1，并且立刻落盘。
            # 若改成「每轮开始时给所有待渲染帧 +1」，崩在第 N 帧会让后面根本没轮到
            # 的帧也被记一次尝试，额度被白白耗光 → 续跑直接失效（实测踩到）。
            frame = int(ev["frame"])
            state.note_attempt(frame)
            state.save()
            emit("frame_start", frame=frame, index=ev.get("index"),
                 total=ev.get("total"), path=ev.get("path"))
        elif kind == "frame_done":
            frame = int(ev["frame"])
            secs = ev.get("secs")
            warmup = self._fed_first_frame
            self._fed_first_frame = False
            if isinstance(secs, (int, float)):
                self.eta.add(secs, warmup=warmup)
            state.mark_done(frame, secs=secs, path=ev.get("path"))
            state.save()
            remaining = len(state.remaining(self.cfg.max_frame_attempts))
            snap = self.eta.snapshot(remaining)
            emit("frame_done", frame=frame, secs=secs, path=ev.get("path"),
                 warmup=warmup, done=state.done_count, total=state.total,
                 remaining=remaining, eta_sec=snap["eta_sec"], eta_mode=snap["mode"],
                 per_frame=snap["per_frame"])
        elif kind == "frame_error":
            frame = int(ev["frame"])
            err = ev.get("err", "")
            state.mark_failed(frame, err)
            state.save()
            emit("frame_error", frame=frame, err=err)
        elif kind == "all_done":
            emit("run_finished", ok=ev.get("ok"), failed=ev.get("failed"), n=ev.get("n"))
        else:
            emit("driver_event", kind=kind, raw=ev)

    def _handle_native(self, line, emit):
        info = self.parser.feed(line)
        ev = {"type": "native", "line": line}
        if info:
            ev.update(info)
        emit("native", **{k: v for k, v in ev.items() if k != "type"})

    # ---------------- 主循环 ----------------
    def run(self, on_event=None, poll_interval=0.25, stall_after=DEFAULT_STALL_AFTER):
        """跑完整轮（含续跑）。返回一个结果 dict，不抛异常。"""
        cfg = self.cfg
        t_start = time.time()
        result = {
            "ok": False, "done": [], "failed": {}, "exhausted": [],
            "elapsed": 0.0, "restarts": 0, "state_path": None,
            "cancelled": False, "error": None, "reporter_error": None,
        }

        def emit(kind, **kw):
            kw["type"] = kind
            if callable(on_event):
                try:
                    on_event(kind, kw)
                except Exception as e:
                    # 回调出错不该把渲染搞挂，但**必须留痕**：
                    # 这里原来是静默 `pass`，结果 CLI 遇到控制台编码问题时（`✗` 在 cp936 下
                    # 无法编码 → UnicodeEncodeError）一个字的错误信息都打不出来，
                    # 表现成"静默退出、只返回 1"，白白排查了很久。
                    if result.get("reporter_error") is None:
                        result["reporter_error"] = "%s: %s" % (type(e).__name__, e)

        # ---- 日志 ----
        if cfg.log_path:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(cfg.log_path)) or ".", exist_ok=True)
                self._log_fp = io.open(cfg.log_path, "a", encoding="utf-8", newline="\n")
            except OSError as e:
                emit("warn", msg="日志文件打不开：%r" % (e,))

        try:
            if not os.path.exists(cfg.blend):
                raise RuntimeError("找不到工程文件：%s" % cfg.blend)
            if not os.path.exists(self.blender_exe):
                raise RuntimeError("找不到 blender.exe：%s" % self.blender_exe)
            if "####" not in cfg.output_template:
                # 不校验的话所有帧会互相覆盖，只会剩最后一张 —— 且很难察觉
                raise RuntimeError("输出模板必须含 ####（帧号占位符），当前：%s"
                                   % cfg.output_template)
            out_dir = os.path.dirname(os.path.abspath(cfg.output_template))
            os.makedirs(out_dir or ".", exist_ok=True)

            state_path = self._init_state(emit)
            result["state_path"] = state_path

            # ---- 释放 driver + job.json 到临时目录（onefile 下必须这么做）----
            driver_path = os.path.join(self.workdir, "brc_driver.py")
            with io.open(driver_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(read_driver_source(self.driver_path))
            job_json = os.path.join(self.workdir, "job.json")

            emit("job_start", total=self.state.total, frames=list(self.state.frames),
                 blend=cfg.blend, output=cfg.output_template, state_path=state_path,
                 scene=cfg.scene,
                 unlimited_restarts=cfg.unlimited_restarts,
                 max_restarts=cfg.max_restarts if not cfg.unlimited_restarts else None,
                 max_no_progress_rounds=cfg.max_no_progress_rounds)

            pending = self.state.remaining(cfg.max_frame_attempts)
            restarts = 0
            no_progress = 0
            while pending:
                if self._cancel.is_set():
                    break
                if restarts > 0:
                    emit("restart", restart_index=restarts, pending=pending)
                    time.sleep(cfg.restart_delay)

                with io.open(job_json, "w", encoding="utf-8", newline="\n") as f:
                    json.dump({
                        "frames": pending,
                        "output_template": cfg.output_template,
                        "engine": cfg.engine,
                        "samples": cfg.samples,
                        "device": cfg.device,
                        "resolution": list(cfg.resolution) if cfg.resolution else None,
                        "resolution_percentage": cfg.resolution_percentage,
                        "file_format": cfg.file_format,
                        "scene": cfg.scene,
                    }, f, ensure_ascii=False)

                cmd = self.cmd_factory(self.blender_exe, cfg.blend, driver_path,
                                       job_json, cfg.extra_args)
                emit("run_start", restart_index=restarts, pending=pending, cmd=cmd)

                rc = self._run_once(cmd, emit, poll_interval, stall_after)
                pending_after = self.state.remaining(cfg.max_frame_attempts)

                if rc == 0 and not pending_after:
                    break
                if self._cancel.is_set():
                    break

                # 没崩但还有剩余帧：说明 driver 提前退出（比如被 -a 参数带偏），
                # 同样按续跑处理，只是次数照样计数
                restarts += 1
                if rc != 0:
                    emit("crash", rc=rc, restart_index=restarts,
                         remaining=pending_after, done=self.state.done_count,
                         total=self.state.total)
                else:
                    emit("warn", msg="子进程正常退出但仍有 %d 帧未完成，继续渲染"
                         % len(pending_after))

                # ---- 护栏：连续多轮一帧都没推进 ----
                # 「一直重启直到完成」模式下，这是唯一能兜住死循环的终止条件：
                # 崩溃是随机的，但"崩在同一处、一帧都渲不出来"说明改重试也没用。
                if len(pending_after) < len(pending):
                    no_progress = 0
                else:
                    no_progress += 1
                if cfg.max_no_progress_rounds > 0 and no_progress >= cfg.max_no_progress_rounds:
                    emit("no_progress", rounds=no_progress, remaining=pending_after,
                         done=self.state.done_count, total=self.state.total)
                    break

                if not cfg.unlimited_restarts and restarts > cfg.max_restarts:
                    emit("give_up", restarts=restarts, remaining=pending_after)
                    break
                pending = pending_after

            # ---- 收尾 ----
            result["done"] = sorted(self.state.done)
            result["failed"] = dict(self.state.failed)
            result["exhausted"] = self.state.exhausted(cfg.max_frame_attempts)
            result["restarts"] = restarts
            result["stopped_by_no_progress"] = (cfg.max_no_progress_rounds > 0
                                                and no_progress >= cfg.max_no_progress_rounds)
            result["cancelled"] = self._cancel.is_set()
            result["ok"] = self.state.is_complete()
            result["total"] = self.state.total
            result["elapsed"] = round(time.time() - t_start, 2)
            emit("job_done", **result)
        except KeyboardInterrupt:
            # Ctrl+C：先把子进程树杀干净，再按「已取消」收尾（每帧已落盘，进度不会丢）
            self.cancel()
            result["cancelled"] = True
            result["error"] = "用户中断"
            emit("job_error", error="用户中断")
        except Exception as e:
            result["error"] = "%s: %s" % (type(e).__name__, e)
            emit("job_error", error=result["error"])
        finally:
            result["elapsed"] = round(time.time() - t_start, 2)
            if self._log_fp:
                try:
                    self._log_fp.close()
                except Exception:
                    pass
                self._log_fp = None
            self._cleanup()
        return result

    def _run_once(self, cmd, emit, poll_interval, stall_after):
        """起一次子进程并消化它的输出，返回退出码。"""
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "bufsize": 0}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except OSError as e:
            emit("warn", msg="启动 Blender 失败：%r" % (e,))
            return 127
        self._proc = proc

        q = queue.Queue()
        t = threading.Thread(target=self._pump, args=(proc, q), daemon=True)
        t.start()

        self._stall_notified = False
        self._last_line_at = time.time()
        self._fed_first_frame = True

        while True:
            try:
                item = q.get(timeout=poll_interval)
            except queue.Empty:
                if not t.is_alive() and q.empty():
                    break
                if stall_after and not self._stall_notified:
                    idle = time.time() - self._last_line_at
                    if idle >= stall_after:
                        self._stall_notified = True
                        emit("stall", idle_sec=round(idle, 1),
                             phase=self.parser.phase or "未知阶段")
                continue
            if item is _SENTINEL:
                break
            self._last_line_at = time.time()
            self._stall_notified = False
            if self._log_fp:
                try:
                    self._log_fp.write(item + "\n")
                except Exception:
                    pass
            if MARK in item:
                try:
                    ev = json.loads(item.split(MARK, 1)[1].strip())
                except ValueError:
                    emit("warn", msg="进度行解析失败：%s" % item[:120])
                    continue
                self._handle_json(ev, emit)
            else:
                self._handle_native(item, emit)

        try:
            rc = proc.wait(timeout=30)
        except Exception:
            self._terminate()
            try:
                rc = proc.wait(timeout=30)
            except Exception:
                rc = -1
        t.join(timeout=5)
        try:
            proc.stdout.close()
        except Exception:
            pass
        self._proc = None
        return rc

    def _cleanup(self):
        """临时目录清理失败不算任务失败（沙箱可能拦截删除）。"""
        if not self._owns_workdir or self._keep_workdir:
            return
        try:
            shutil.rmtree(self.workdir)
        except Exception:
            pass
