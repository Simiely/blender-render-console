# -*- coding: utf-8 -*-
"""原生输出折叠与日志待消费队列。

唯一变更原因是"长任务日志怎么不刷屏、不爆内存、不丢证据"。
不碰 tkinter，可单测。
"""

import re
import time
from collections import deque


class NativeLineFilter(object):
    """Blender 原生输出的**折叠器**（日志面板用，纯逻辑可单测）。

    为什么需要它：`-b` 下 Blender 的采样进度是用 `\\r` 原地刷新的，`core._pump` 会把它切成
    一行行推过来 —— 一帧 64 采样就是几十行，一个四小时的任务能吐几万行。
    原生输出默认开启之后，这些行会瞬间刷爆日志面板，更要紧的是**把真正的 traceback 冲走**。

    三条规则（都钉在单测里）：

    1. **错误行永不折叠**：含 error / traceback / warning / can't find / failed 的行原样放行
       —— 它们才是排错时要看的东西；
    2. **同类进度行按「帧号」折叠**，且两次输出间隔不少于 `interval` 秒；
       被折掉的行数累加，附在下一条输出的**前面**（`… 上一段进度已折叠 N 行`）；
    3. **换类时先把账交代清楚**：不交代的话，"折叠了多少行"就永远看不见了。

    只折叠采样进度（`Sample 12/64` / `Rendering 12 / 64 samples`）。别的原生行
    （阶段、Saved、着色器编译）本来就不重复，逐行显示才有信息量。
    """

    IMPORTANT = re.compile(
        r"error|traceback|warning|exception|failed|can't find|cannot|not found|"
        r"no such file|invalid", re.I)
    SAMPLE = re.compile(r"Sample\s+\d+\s*/\s*\d+|Rendering\s+\d+\s*/\s*\d+\s+samples",
                        re.I)
    FRAME = re.compile(r"Fra:\s*(-?\d+)")

    # 工程自身资源引用问题产生的**已知无害**告警。每一帧都会重复一次，
    # 390 帧能刷近 2000 行，把真正的 traceback 冲走 —— 但直接删又违反
    # 「静默吞异常必须留痕」的规矩，所以折叠成一条汇总（见 _benign_summary）。
    #
    # 正则刻意写得很窄，只匹配这三种精确文案：
    #   1) Blender 找不到贴图文件（路径是工程自己的，工具改不了）
    #   2) 上一条的每帧汇总 "Failed to load N image files"
    #   3) 贴图缺失连带出来的色彩空间告警（colorspace 为空）
    # 不属于这三类的一律照常显示 —— 真正的 Error / Traceback 绝不能被吞。
    BENIGN = re.compile(
        r"WARNING Image file .*? does not exist\."
        r"|ERROR Failed to load \d+ image files"
        r"|WARNING Colorspace .*?(?:not found|can't be converted)",
        re.I)

    def __init__(self, interval=1.0, clock=None):
        self.interval = interval
        # 注入时钟是为了能单测：不真的 sleep，直接把时间"往前拨"
        self._clock = clock or time.time
        self._key = None
        self._last_emit = 0.0
        self._suppressed = 0
        self._benign = 0          # 折叠掉的无害告警行数（留痕用）

    def reset(self):
        """新任务开始时清账（否则上一轮漏下的折叠数会算到这一轮头上）。"""
        self._key = None
        self._last_emit = 0.0
        self._suppressed = 0
        self._benign = 0

    def _benign_summary(self):
        n, self._benign = self._benign, 0
        return ("… 已折叠 %d 行工程贴图/色彩空间告警（工程自身资源缺失，不影响渲染；"
                "完整的原始输出见 --log 指定的日志文件）" % n)

    def _collapse_key(self, line):
        """同类行的判据；返回 None = 这一行不参与折叠。"""
        if not self.SAMPLE.search(line):
            return None
        m = self.FRAME.search(line)
        return ("sample", m.group(1) if m else "?")

    def _take_summary(self, lines):
        """把待交代的折叠数拼到这批输出最前面。"""
        out = lines
        if self._benign:
            out = [self._benign_summary()] + out
        if not self._suppressed:
            return out
        n, self._suppressed = self._suppressed, 0
        return ["… 上一段进度已折叠 %d 行（同类进度行只保留最新）" % n] + out

    def feed(self, line):
        """消化一行原生输出 → 该显示的行（可能为空列表 = 被折掉了）。"""
        line = (line or "").rstrip()
        if not line:
            return []
        # ⓪ 已知无害的工程资源告警：累计折叠，不逐行刷屏。
        #    必须放在 IMPORTANT 之前 —— 这几条本身含 warning/error，
        #    但只匹配精确文案，别的 Error / Traceback 都落不到这里。
        if self.BENIGN.search(line):
            self._benign += 1
            return []
        # ① 错误/警告：原样放行（即使和上一行一模一样也要放，重复的 traceback 也是证据）
        if self.IMPORTANT.search(line):
            return self._take_summary([line])
        key = self._collapse_key(line)
        # ② 非采样行：逐行放行
        if key is None:
            return self._take_summary([line])
        # ③ 采样行：同帧且间隔不够 → 折掉，只累计
        now = self._clock()
        if key == self._key and (now - self._last_emit) < self.interval:
            self._suppressed += 1
            return []
        out = self._take_summary([line])
        self._key, self._last_emit = key, now
        return out

    def flush(self):
        """任务结束时把最后一段的表达清楚（不然尾部折叠数就丢了）。"""
        out = []
        if self._benign:
            out.append(self._benign_summary())
        return out + self._take_summary([])


class LogModel(object):
    """日志面板的**待消费队列**。

    长任务会吐几万行，所以模型只攒"还没被界面取走的行"（界面按帧率批量取）。
    行数上限有两道：
    - 这里 `maxlen` 兜住**内存**（界面来不及取时不能无限涨）；
    - 界面 Text 控件另有 `LOG_MAX_LINES` 兜住**渲染开销**。

    丢掉的行数记在 `dropped` 里、由界面明说一句 —— 静默丢日志等于静默丢证据。
    """

    def __init__(self, capacity=4000, maxlen=20000):
        self.capacity = capacity
        self.maxlen = maxlen
        self._pending = deque()
        self.total = 0                 # 累计收到的行数（界面对账用）
        self.dropped = 0               # 因为 maxlen 上限被丢掉的最早行数

    def add(self, text):
        for line in str(text).replace("\r", "\n").split("\n"):
            if line.strip():
                if self.maxlen and len(self._pending) >= self.maxlen:
                    self._pending.popleft()
                    self.dropped += 1
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
        self.dropped = 0               # 清空日志 = 重新开始记账

    def __len__(self):
        return len(self._pending)
