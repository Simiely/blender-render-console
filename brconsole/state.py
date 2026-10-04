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


class JobState(object):
    """一次渲染任务的断点状态。"""

    def __init__(self, path, blend="", frames=(), output_template="", engine=None,
                 samples=None, device=None, resolution=None, file_format=None):
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
        except (OSError, ValueError):
            # 状态文件坏了宁可从头来，也不要让程序起不来
            return None
        st = cls(path)
        for k in ("version", "blend", "output_template", "engine", "samples",
                  "device", "file_format", "created", "updated"):
            if k in d:
                setattr(st, k, d[k])
        st.frames = [int(x) for x in d.get("frames", [])]
        res = d.get("resolution")
        st.resolution = list(res) if res else None
        st.done = {int(k): v for k, v in (d.get("done") or {}).items()}
        st.failed = {int(k): v for k, v in (d.get("failed") or {}).items()}
        st.attempts = {int(k): int(v) for k, v in (d.get("attempts") or {}).items()}
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
