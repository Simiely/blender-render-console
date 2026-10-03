# -*- coding: utf-8 -*-
"""命令行入口 —— 本阶段的可交付版本（GUI 是下一步）。

    python main.py 工程.blend -s 1 -e 240 -E CYCLES --samples 256 --device OPTIX

行为：实时打印每帧耗时与 ETA；Blender 崩了自动重启并从断点接着渲染。
"""

import argparse
import os
import sys
import time

from . import locate
from .core import JobConfig, RenderJob
from .eta import fmt_duration


def parse_frames(spec, start=None, end=None, step=1):
    """`1-10,15,20-25` / `1:240` → 帧号列表。start/end 可单独给。"""
    frames = []
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
    if start is not None or end is not None:
        s = int(start) if start is not None else (min(frames) if frames else 1)
        e = int(end) if end is not None else (max(frames) if frames else s)
        if e < s:
            s, e = e, s
        frames = frames + list(range(s, e + 1, step))
    seen, out = set(), []
    for f in frames:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return sorted(out)


def build_parser():
    p = argparse.ArgumentParser(
        prog="blender-render-console",
        description="无头调用 Blender 渲染：实时进度 + ETA + 崩溃自动续跑")
    p.add_argument("blend", help=".blend 工程文件")
    p.add_argument("-s", "--start", type=int, help="起始帧")
    p.add_argument("-e", "--end", type=int, help="结束帧")
    p.add_argument("--step", type=int, default=1, help="帧步长（默认 1）")
    p.add_argument("-f", "--frames", help="显式帧列表，如 1-10,15,20-25")
    p.add_argument("-o", "--output", help="输出模板（含 ####，如 out/frame_####）或输出目录")
    p.add_argument("-E", "--engine", help="引擎：CYCLES / BLENDER_EEVEE / BLENDER_WORKBENCH")
    p.add_argument("--samples", type=int, help="采样数（Cycles 渲染采样 / EEVEE TAA）")
    p.add_argument("--device", help="Cycles 设备：CPU / CUDA / OPTIX / HIP / ONEAPI")
    p.add_argument("--res", help="分辨率，如 1920x1080")
    p.add_argument("--pct", type=int, help="分辨率百分比，如 50")
    p.add_argument("--format", dest="file_format", help="输出格式：PNG / JPEG / OPEN_EXR ...")
    p.add_argument("--blender", help="blender.exe 路径（不给就自动探测）")
    p.add_argument("--deep-scan", action="store_true", help="自动探测时连便携版目录一起扫（慢）")
    p.add_argument("--state", help="断点状态文件路径（默认输出目录下 .render_state.json）")
    p.add_argument("--log", help="Blender 原始输出落盘路径")
    p.add_argument("--max-restarts", type=int, default=5, help="最多重启几次（默认 5）")
    p.add_argument("--max-frame-attempts", type=int, default=3, help="单帧最多尝试几次（默认 3）")
    p.add_argument("--no-resume", action="store_true", help="忽略已有断点，从头渲染")
    p.add_argument("--verbose", action="store_true", help="打印 Blender 原生输出")
    p.add_argument("--keep-workdir", action="store_true", help="保留临时目录（排错用）")
    return p


class ConsoleReporter(object):
    """把事件打到终端：进度行原地刷新，其他事件各自换行。"""

    def __init__(self, verbose=False, stream=None):
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.last_len = 0
        self.last_phase = None

    def _clear(self):
        if self.last_len:
            self.stream.write("\r" + " " * self.last_len + "\r")
            self.last_len = 0

    def _line(self, text):
        self._clear()
        self.stream.write(text + "\n")
        self.stream.flush()

    def _progress(self, text):
        pad = " " * max(0, self.last_len - len(text))
        self.stream.write("\r" + text + pad)
        self.stream.flush()
        self.last_len = len(text)

    def __call__(self, kind, ev):
        if kind == "native":
            phase = ev.get("phase")
            if phase and phase != self.last_phase:
                self.last_phase = phase
                self._line("  · %s" % phase)
            if self.verbose:
                self._line("  | %s" % ev.get("line", ""))
            return
        if kind == "frame_done":
            eta = fmt_duration(ev.get("eta_sec"))
            warm = " (预热帧，不计入基线)" if ev.get("warmup") else ""
            self._line("[%d/%d] 帧 %d 完成 %s%s | 单帧 %s | 剩余 %d 帧 | ETA %s（%s）"
                       % (ev.get("done", 0), ev.get("total", 0), ev.get("frame"),
                          fmt_duration(ev.get("secs")), warm,
                          fmt_duration(ev.get("per_frame")),
                          ev.get("remaining", 0), eta, ev.get("eta_mode", "")))
            return
        if kind == "frame_start":
            self._progress("  渲染帧 %d ..." % ev.get("frame"))
            return
        if kind == "frame_error":
            self._line("  ✗ 帧 %s 渲染失败：%s" % (ev.get("frame"), ev.get("err")))
            return
        if kind == "crash":
            self._line("  ⚠ Blender 退出码 %s（第 %s 次重启），已完成 %s/%s，还剩 %s 帧"
                       % (ev.get("rc"), ev.get("restart_index"),
                          ev.get("done"), ev.get("total"), len(ev.get("remaining") or [])))
            return
        if kind == "warn":
            self._line("  ! %s" % ev.get("msg", ""))
            return
        if kind == "stall":
            self._line("  … 已 %ss 无输出（当前：%s），大场景同步属于正常现象"
                       % (ev.get("idle_sec"), ev.get("phase")))
            return
        if kind == "resume":
            self._line("  断点续跑：已完成 %s/%s 帧 - %s"
                       % (len(ev.get("done") or []), ev.get("total"), ev.get("state_path")))
            return
        if kind == "restart":
            self._line("  第 %s 次重启，剩余帧 %s" % (ev.get("restart_index"), ev.get("pending")))
            return
        if kind == "settings":
            self._line("  实际设置：引擎 %s | %sx%s @%s%% | 采样 %s | 输出 %s"
                       % (ev.get("engine"), (ev.get("res") or [0, 0])[0],
                          (ev.get("res") or [0, 0])[1], ev.get("pct"),
                          ev.get("samples"), ev.get("output")))
            return
        if kind == "job_start":
            self._line("任务开始：%s\n  帧数 %d | 输出 %s | 断点 %s"
                       % (ev.get("blend"), ev.get("total"), ev.get("output"),
                          ev.get("state_path")))
            return
        if kind == "job_done":
            self._line("任务结束：完成 %d/%d 帧 | 失败 %d | 放弃 %d | 重启 %d 次 | 用时 %s"
                       % (len(ev.get("done") or []), ev.get("total", 0),
                          len(ev.get("failed") or {}), len(ev.get("exhausted") or []),
                          ev.get("restarts", 0), fmt_duration(ev.get("elapsed"))))
            if ev.get("cancelled"):
                self._line("  已取消（进度已保存，下次同命令可续跑）")
            elif not ev.get("ok"):
                self._line("  未完成 —— 再次执行同一条命令即可从断点继续")
            return
        if kind == "job_error":
            self._line("  ✗ %s" % ev.get("error"))
            return
        if kind == "give_up":
            self._line("  重启次数已达上限，停止。剩余帧 %s" % ev.get("remaining"))
            return
        if kind == "device_set":
            self._line("  渲染设备：%s（%s）" % (ev.get("type"), ", ".join(ev.get("devices") or [])))
            return
        if kind == "engine_set":
            if ev.get("requested") != ev.get("actual"):
                self._line("  引擎 %s → 实际 %s" % (ev.get("requested"), ev.get("actual")))
            return


def main(argv=None):
    args = build_parser().parse_args(argv)

    blend = os.path.abspath(args.blend)
    if not os.path.exists(blend):
        print("找不到工程文件：%s" % blend)
        return 2

    # ---- 帧范围 ----
    frames = parse_frames(args.frames, args.start, args.end, args.step)
    if not frames:
        print("没有指定帧范围：用 -s/-e 或 -f")
        return 2

    # ---- 输出模板 ----
    if args.output:
        out = args.output
        if os.path.isdir(out) or out.endswith(("\\", "/")):
            out = os.path.join(out, "frame_####")
        elif "####" not in out:
            base, ext = os.path.splitext(out)
            out = base + "_####" + ext
    else:
        out = os.path.join(os.path.dirname(blend),
                           os.path.splitext(os.path.basename(blend))[0] + "_####")
    out = os.path.abspath(out)

    # ---- Blender ----
    try:
        blender, cands = locate.resolve_blender(args.blender, deep=args.deep_scan)
    except SystemExit as e:
        print(e)
        return 2
    if not blender:
        print("没找到 blender.exe —— 用 --blender 指定路径。已扫描：%s"
              % ", ".join(locate.COMMON_ROOTS))
        return 2
    if len(cands) > 1:
        print("检测到多个 Blender，使用：%s（其余：%s）"
              % (blender, ", ".join(cands[1:3])))
    else:
        print("Blender：%s" % blender)

    # ---- 分辨率 ----
    resolution = None
    if args.res:
        try:
            w, h = args.res.lower().split("x")
            resolution = (int(w), int(h))
        except ValueError:
            print("--res 格式应为 宽x高，如 1920x1080")
            return 2

    cfg = JobConfig(
        blend=blend, frames=frames, output_template=out, engine=args.engine,
        samples=args.samples, device=args.device, resolution=resolution,
        resolution_percentage=args.pct, file_format=args.file_format,
        state_path=args.state, log_path=args.log,
        max_restarts=args.max_restarts, max_frame_attempts=args.max_frame_attempts,
        resume=not args.no_resume)

    job = RenderJob(cfg, blender, keep_workdir=args.keep_workdir)
    reporter = ConsoleReporter(verbose=args.verbose)

    t0 = time.time()
    try:
        result = job.run(on_event=reporter)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止…")
        job.cancel()
        print("已取消，用时 %s（进度已保存，再跑同一条命令可续跑）"
              % fmt_duration(time.time() - t0))
        return 130

    return 0 if result.get("ok") else 1
