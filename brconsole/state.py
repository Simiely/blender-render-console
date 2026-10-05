# -*- coding: utf-8 -*-
"""断点状态文件。

崩溃续跑的全部依据都在这一个 JSON 里：哪些帧已完成（含产物路径与耗时）、
哪些帧失败、每帧被尝试过几次。

落盘必须**原子**：先写同目录的 `.tmp` 再 `os.replace`。渲染中途被杀是常态，
半截写坏的 state 会让"已完成"的帧集体丢失，比崩溃本身更糟。
"""

import json
import os
import tempfile
import time

STATE_VERSION = 1

#: 断点文件的默认文件名（`JobConfig.state_file()` 与界面的 `guess_state_path()` 共用一份规则）
DEFAULT_STATE_FILENAME = ".render_state.json"


def task_signature(blend, output_template, frames, scene):
    """「这是不是同一个任务」的判据：工程 + 输出模板 + 帧范围 + 场景。

    **四个字段缺一不可**：同一个工程换场景，帧号往往一模一样，但产物完全是另一张图 ——
    少了场景这一项就会把上一个场景的进度算进来（产物全错还看不出来）。

    为什么放在这个叶子模块：`core`（运行时续跑）与 `taskstore`（开机续跑）都要做这个判断，
    原先各自手写了一遍这四个字段。两处一旦漂开，**"开机能不能续跑"会和"运行时能不能续跑"
    给出不同答案，而且两边都不报错** —— 所以收成一份（同一个规则的唯一来源）。
    `None` / 空值一律归一化：坏存档应被判成"不是同一任务"（从头渲染），而不是在
    `os.path.abspath(None)` 上抛异常 —— 后者在开机那条无人值守的路径上会静默什么都不做。
    """
    return (os.path.abspath(blend) if blend else "",
            output_template or "",
            tuple(int(f) for f in (frames or ())),
            scene or "")


class JobState(object):
    """一次渲染任务的断点状态。"""

    def __init__(self, path, blend="", frames=(), output_template="", engine=None,
                 samples=None, device=None, resolution=None, file_format=None, scene=None):
        self.path = path
        self.version = STATE_VERSION
        self.blend = blend
        self.frames = list(frames)
        self.output_template = output_template
        self.engine = engine
        self.samples = samples
        self.device = device
        self.resolution = list(resolution) if resolution else None
        self.file_format = file_format
        # 渲染的场景名；空 = 工程里激活的那个。换场景不能续跑（帧号一样但内容不同）
        self.scene = scene or ""
        self.created = time.time()
        self.updated = time.time()
        # 键在内存里是 int，落盘时转 str（JSON 只接受字符串键）
        self.done = {}        # frame -> {"secs": float, "path": str, "t": float}
        self.failed = {}      # frame -> {"err": str, "t": float}
        self.attempts = {}    # frame -> int

    # ---------- 存取 ----------
    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            st = cls(path)
            for k in ("version", "blend", "output_template", "engine", "samples",
                      "device", "file_format", "scene", "created", "updated"):
                if k in d:
                    setattr(st, k, d[k])
            st.frames = [int(x) for x in d.get("frames", [])]
            res = d.get("resolution")
            st.resolution = list(res) if res else None
            st.done = {int(k): v for k, v in (d.get("done") or {}).items()}
            st.failed = {int(k): v for k, v in (d.get("failed") or {}).items()}
            st.attempts = {int(k): int(v) for k, v in (d.get("attempts") or {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            # ⚠️ 类型转换**必须在 try 里面**：JSON 语法没错、但字段值不对（帧号写成字符串、
            #    done 的键不是数字、attempts 值不是数字）同样算"文件坏了"。
            #    这些转换原先写在 try 外面，于是"坏了就当没有"这个承诺只兑现了一半 ——
            #    实测会直接抛 ValueError 出去，把渲染和开机认领都带崩（2026-10-05 修复）。
            #    文档承诺：状态文件坏了宁可从头来，也不要让程序起不来。
            return None
        return st

    def to_dict(self):
        return {
            "version": self.version,
            "blend": self.blend,
            "frames": self.frames,
            "output_template": self.output_template,
            "engine": self.engine,
            "samples": self.samples,
            "device": self.device,
            "resolution": self.resolution,
            "file_format": self.file_format,
            "scene": self.scene,
            "created": round(self.created, 3),
            "updated": round(time.time(), 3),
            "done": {str(k): v for k, v in sorted(self.done.items())},
            "failed": {str(k): v for k, v in sorted(self.failed.items())},
            "attempts": {str(k): v for k, v in sorted(self.attempts.items())},
        }

    def save(self):
        """原子落盘：同目录 tmp + os.replace。"""
        d = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".brc-state-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise

    # ---------- 变更 ----------
    def note_attempt(self, frame):
        self.attempts[int(frame)] = self.attempts.get(int(frame), 0) + 1

    def mark_done(self, frame, secs=None, path=None):
        self.done[int(frame)] = {
            "secs": secs, "path": path or "", "t": round(time.time(), 3),
        }
        self.failed.pop(int(frame), None)

    def mark_failed(self, frame, err=""):
        self.failed[int(frame)] = {"err": str(err)[:500], "t": round(time.time(), 3)}

    # ---------- 查询 ----------
    def signature(self):
        """这个断点属于哪个任务（与 `JobConfig.signature()` 可比）。"""
        return task_signature(self.blend, self.output_template, self.frames, self.scene)

    def remaining(self, max_attempts=3, frames=None):
        """还剩哪些帧要渲染：未完成 且 未失败 且 还有重试额度。

        `max_attempts <= 0` 表示**不限次数**（配合「一直重启直到渲完」使用 ——
        否则单帧额度会比重启次数先用完，那个选项就形同虚设）。
        """
        pool = list(frames) if frames else list(self.frames)
        out = []
        for f in pool:
            f = int(f)
            if f in self.done:
                continue
            if f in self.failed:
                continue
            if max_attempts > 0 and self.attempts.get(f, 0) >= max_attempts:
                continue
            out.append(f)
        return sorted(set(out))

    def exhausted(self, max_attempts=3):
        """重试额度耗尽的帧 —— 界面要把它们明确说成「放弃」，而不是静默跳过。"""
        if max_attempts <= 0:
            return []
        return sorted(f for f, n in self.attempts.items()
                      if n >= max_attempts and f not in self.done)

    @property
    def done_count(self):
        return len(self.done)

    @property
    def total(self):
        return len(self.frames)

    def is_complete(self):
        return all(int(f) in self.done for f in self.frames)

    def stats(self):
        secs = [v["secs"] for v in self.done.values() if isinstance(v.get("secs"), (int, float))]
        return {
            "total": self.total,
            "done": self.done_count,
            "failed": len(self.failed),
            "min": min(secs) if secs else None,
            "max": max(secs) if secs else None,
            "sum": sum(secs) if secs else None,
        }
