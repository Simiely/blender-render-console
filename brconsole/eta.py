# -*- coding: utf-8 -*-
"""ETA 估算。

两条硬规则（来自实测，见 DEVELOPMENT.md）：

1. **首帧必须从基线里剔除** —— 它包含 shader / kernel 编译预热，是一次性成本。
   实测 EEVEE 首帧 10.25s 而后续帧近乎瞬时，不剔除会让 ETA 虚高几十倍。
2. **不用 Blender 自带的 Remaining** —— 5.2 里它是**帧内**剩余，不是队列总剩余。

算法：剔除 warmup → 单帧耗时 EMA 平滑（α≈0.3）→ `剩余帧数 × 平滑耗时`。
样本极少（≤ 2）时才退回用原始均值，因为 EMA 初值本身不可信。
"""

import statistics


class EtaEstimator(object):
    """单帧耗时的 EMA 平滑器 + 剩余时间外推。

    warmup_frames=1 表示第 1 帧（按传入顺序）不计入基线。
    """

    def __init__(self, alpha=0.3, warmup_frames=1):
        self.alpha = alpha
        self.warmup_frames = warmup_frames
        self.ema = None
        self.samples = []          # 计入基线的样本（已剔除 warmup）
        self.warmups = []          # 被剔除的样本，仅用于展示
        self.n_seen = 0

    def add(self, secs, warmup=None):
        """喂一个单帧耗时。`warmup=None` 时按序号自动判定。"""
        if warmup is None:
            warmup = self.n_seen < self.warmup_frames
        self.n_seen += 1
        if warmup:
            self.warmups.append(float(secs))
            return False
        v = float(secs)
        self.samples.append(v)
        self.ema = v if self.ema is None else self.alpha * v + (1 - self.alpha) * self.ema
        return True

    # ---- 基线口径 ----
    @property
    def has_baseline(self):
        return bool(self.samples)

    @property
    def per_frame(self):
        """当前采用的单帧耗时（秒）；无基线返回 None。"""
        if not self.samples:
            return None
        if len(self.samples) <= 2:
            # 样本太少时 EMA 还没收敛，直接用均值更诚实
            return statistics.fmean(self.samples)
        return self.ema

    @property
    def mode(self):
        """当前口径文案，界面上用来告诉用户这个 ETA 有多可信。"""
        if not self.samples:
            return "尚无样本"
        if len(self.samples) <= 2:
            return "均值（样本 %d）" % len(self.samples)
        return "EMA（样本 %d）" % len(self.samples)

    @property
    def mean(self):
        return statistics.fmean(self.samples) if self.samples else None

    @property
    def median(self):
        return statistics.median(self.samples) if self.samples else None

    def estimate(self, remaining_frames):
        """剩余帧数 → 剩余秒数；无法估算返回 None。"""
        if remaining_frames <= 0:
            return 0.0
        per = self.per_frame
        # ⚠️ 必须 `is None` 判，**不能写 `if not per`** —— 单帧耗时恰为 0 时
        # 会被当成"没有基线"，把 0 秒的 ETA 显示成"--"。
        # 这是"用真值判断数字"的老坑，和 locate.py 里 `if deadline:` 遇 0 为假同族。
        if per is None:
            return None
        return per * remaining_frames

    def eta_epoch(self, remaining_frames):
        """返回 (剩余秒数, 口径文案)；无法估算时秒数为 None。"""
        return self.estimate(remaining_frames), self.mode

    def snapshot(self, remaining_frames):
        """给界面用的一揽子数据。"""
        return {
            "remaining_frames": remaining_frames,
            "eta_sec": self.estimate(remaining_frames),
            "mode": self.mode,
            "per_frame": self.per_frame,
            "mean": self.mean,
            "median": self.median,
            "n_samples": len(self.samples),
            "warmup_secs": (self.warmups[0] if self.warmups else None),
        }


def fmt_duration(secs):
    """秒 → `1h02m03s` / `02m03s` / `3.4s`。None → `--`。"""
    if secs is None:
        return "--"
    try:
        secs = float(secs)
    except (TypeError, ValueError):
        return "--"
    if secs < 0:
        secs = 0.0
    total = int(round(secs))
    if total < 60:
        return "%.1fs" % secs
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh%02dm%02ds" % (h, m, s)
    return "%02dm%02ds" % (m, s)
