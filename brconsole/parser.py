# -*- coding: utf-8 -*-
r"""Blender 原生输出行 → 结构化信息。

⚠️ 格式基线来自 `probes/probe_cycles.log` / `probes/probe_eevee.log`（Blender 5.2.2 LTS 实测）。
网上流传的 4.x 正则（`Fra:1 Mem:... | Time:... | Remaining:... | Scene, View Layer`）在这里
**命中 0 行**，不要照抄。Blender 大版本升级后第一件事是重跑 `tools/probe_render.py`。

5.2 的行格式：`<秒表 mm:ss.mmm>  <类别>  | <内容>`
  - Cycles 帧内进度：`00:03.906  render | Fra: 4 | Mem: 6M | Sample 24/24`
  - EEVEE 帧内进度：`00:11.765  render | Fra: 1 | Rendering 1 / 64 samples`
  - 落盘：`render | Saved: 'C:\...\probe_0004.png'`

字段顺序不保证固定，所以每条信息单独一个正则，互不假设位置。
"""

import re

# ---- 帧内进度 ----
RE_FRAME = re.compile(r"Fra:\s*(\d+)")
RE_SAMPLE_CYCLES = re.compile(r"Sample\s+(\d+)\s*/\s*(\d+)")
RE_SAMPLE_EEVEE = re.compile(r"Rendering\s+(\d+)\s*/\s*(\d+)\s+samples", re.I)
# Saved 行里的路径可能含空格，取到行尾的最后一个单引号
RE_SAVED = re.compile(r"Saved:\s*'(.+)'\s*$")
# 5.2 的 Remaining 是**帧内**剩余，不是队列总剩余；只做展示，不参与 ETA
RE_REMAINING = re.compile(r"Remaining:\s*([\d:.]+)")
RE_TIME = re.compile(r"\bTime:\s*([\d:.]+)")

# ---- 阶段识别：这几个阶段没有百分比可显示，界面必须给出文案，否则用户以为死机 ----
PHASE_RULES = (
    ("正在编译着色器 / 加载渲染 kernel（首次可能数分钟）",
     (r"Loading render kernels", r"Loading denoising kernels", r"Updating Shaders")),
    ("正在构建 BVH",
     (r"Building BVH", r"Copying BVH to device")),
    ("正在同步场景到渲染设备",
     (r"Synchronizing object", r"Updating Scene\b", r"Updating Mesh\b",
      r"Updating Objects\b", r"Updating Camera", r"Updating Lights",
      r"Updating Images", r"Updating Particle", r"Updating Volume")),
    ("正在降噪 / 合成",
     (r"Denoising", r"Writing image", r"Compositing")),
)

# 帧末统计行（`Time: 00:10.25 (Saving: 00:00.11)`）也含 "Saving:"，但它是**结束**信号，
# 不是"正在进行的阶段"—— 不排除的话界面会在每帧结束闪一次假阶段。
RE_FRAME_FOOTER = re.compile(r"\bTime:\s*[\d:.]+")

_PHASE_COMPILED = tuple(
    (text, tuple(re.compile(p, re.I) for p in pats)) for text, pats in PHASE_RULES
)

# Blender 的秒表前缀（用于从原生行取进程内耗时，仅在需要校准时使用）
RE_STAMP = re.compile(r"^\s*(\d+):(\d{2})\.(\d{3})\s+(\S+)\s*\|\s*(.*)$")


def parse_stamp(line):
    """拆 5.2 的 `<秒表> <类别> | <内容>`。返回 (secs, category, content) 或 None。"""
    m = RE_STAMP.match(line)
    if not m:
        return None
    mm, ss, ms, cat, content = m.groups()
    return int(mm) * 60 + int(ss) + int(ms) / 1000.0, cat, content


def parse_stage(line):
    """识别「没有百分比可显示」的长阶段，返回中文文案或 None。"""
    if RE_FRAME_FOOTER.search(line):
        return None
    for text, pats in _PHASE_COMPILED:
        for p in pats:
            if p.search(line):
                return text
    return None


class NativeParser(object):
    """逐行消化 Blender 原生输出，产出给界面用的增量信息。

    只做「兜底 + 阶段展示」：精确的每帧耗时由 driver 的 ##PROG## JSON 行给出，
    这里的 frame / sample 用于在 JSON 行到达之前也能显示点东西。
    """

    def __init__(self):
        self.frame = None            # 最近一次看到的帧号
        self.sample_cur = None
        self.sample_total = None
        self.phase = None            # 当前阶段文案
        self.saved = []              # 已落盘文件路径
        self.remaining_in_frame = None

    def feed(self, line):
        """消化一行，返回本次发生变化的信息 dict（无变化返回 None）。"""
        out = {}
        m = RE_FRAME.search(line)
        if m:
            f = int(m.group(1))
            if f != self.frame:
                self.frame = f
                out["frame"] = f

        m = RE_SAMPLE_CYCLES.search(line) or RE_SAMPLE_EEVEE.search(line)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            if (cur, tot) != (self.sample_cur, self.sample_total):
                self.sample_cur, self.sample_total = cur, tot
                out["sample"] = (cur, tot)

        m = RE_SAVED.search(line)
        if m:
            path = m.group(1)
            if path not in self.saved:
                self.saved.append(path)
                out["saved"] = path

        m = RE_REMAINING.search(line)
        if m:
            self.remaining_in_frame = m.group(1)
            out["remaining_in_frame"] = self.remaining_in_frame

        stage = parse_stage(line)
        if stage and stage != self.phase:
            self.phase = stage
            out["phase"] = stage

        return out or None
