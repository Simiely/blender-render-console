# -*- coding: utf-8 -*-
"""
实测探针：验证 1) 能否从 Blender stdout 实时读到渲染进度  2) EEVEE 在 -b 下能否渲染

产物（按 Blender 主版本号分文件，避免 5.3 的样本覆盖 5.2 基线）：
    probe_cycles.log / probe_eevee.log        —— 当前版本（脚本探测到的 blender）
    probe_cycles_5.2.log / probe_cycles_5.3.log —— 归档副本，跨版本对照用

用法：
    python tools/probe_render.py                    # 自动探测 blender.exe
    python tools/probe_render.py --blender <path>   # 指定版本（多版本共存时对照用）

⚠️ 大版本升级后必须重跑一次（项目关键坑第 1 条）：5.2 的进度格式与 4.x 不同，
   升级后若格式又变，parser 的正则会静默失配，界面只剩"未知阶段"。
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROBES = os.path.join(ROOT, "probes")            # 归档样本放这里，与 5.2 基线同目录
sys.path.insert(0, ROOT)
from brconsole.locate import resolve_blender  # noqa: E402  单一来源，别再硬编码安装路径

BLEND = os.path.join(HERE, "probe.blend")


def pick_blender(explicit=None):
    """优先用命令行指定的；否则复用工具的自动探测（与运行时同一条规则）。"""
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit("指定的 blender 不存在：%s" % explicit)
        return explicit
    found, _, why = resolve_blender(None, deep=False)
    if not found:
        raise SystemExit(why or "没找到 blender.exe，请用 --blender 指定")
    return found


def blender_major(exe):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=30).stdout
        m = re.search(r"Blender\s+(\d+\.\d+)", out)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def make_scene():
    expr = (
        "import bpy;"
        "sc=bpy.context.scene;"
        "sc.render.engine='CYCLES';"
        "sc.cycles.device='CPU';"
        "sc.cycles.samples=24;"
        "sc.render.resolution_x=320;"
        "sc.render.resolution_y=240;"
        "sc.render.resolution_percentage=100;"
        "sc.frame_start=1;sc.frame_end=4;"
        "sc.render.image_settings.file_format='PNG';"
        "sc.render.filepath=r'%s';"
        "bpy.ops.wm.save_as_mainfile(filepath=r'%s')"
        % (os.path.join(HERE, "probe_####"), BLEND)
    )
    p = subprocess.run([CURRENT_BLENDER, "-b", "--factory-startup", "--python-expr", expr],
                       capture_output=True)
    print("make_scene rc=%s" % p.returncode)


def capture(tag, args, timeout=900):
    """逐块读取 Blender 的 stdout/stderr，把 \\r 拆成 \\n 后落盘，并统计耗时"""
    log = os.path.join(HERE, "probe_%s.log" % tag)
    cmd = [CURRENT_BLENDER] + args
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=0)
    buf = b""
    chunks = []
    while True:
        b = proc.stdout.read(1)
        if not b:
            break
        if b in (b"\r", b"\n"):
            if buf:
                chunks.append((round(time.time() - t0, 2), buf.decode("utf-8", "replace")))
                buf = b""
        else:
            buf += b
    if buf:
        chunks.append((round(time.time() - t0, 2), buf.decode("utf-8", "replace")))
    proc.wait()
    dt = time.time() - t0

    with open(log, "w", encoding="utf-8", newline="\n") as f:
        f.write("CMD: %s\n" % " ".join(cmd))
        f.write("RC: %s   WALL: %.2fs   LINES: %d\n" % (proc.returncode, dt, len(chunks)))
        f.write("=" * 70 + "\n")
        for t, line in chunks:
            if line.strip():
                f.write("[%8.2fs] %s\n" % (t, line))
    # 归档一份带版本号的副本：升级后能拿着新旧两份逐行对照格式差异
    archive = os.path.join(PROBES, "probe_%s_%s.log" % (tag, CURRENT_MAJOR))
    if archive != log:
        with open(log, "r", encoding="utf-8") as src, \
                open(archive, "w", encoding="utf-8", newline="\n") as dst:
            dst.write(src.read())

    # 立即打印关键统计（5.2+ 的进度在 Sample 行上，4.x 的 `| Time:` 正则命中 0 行）
    pat_frame = re.compile(r"Fra:\s*(\d+)")
    pat_sample = re.compile(r"Sample\s+(\d+)\s*/\s*(\d+)")
    frames = [m for _, l in chunks for m in [pat_frame.search(l)] if m]
    samples = [m for _, l in chunks for m in [pat_sample.search(l)] if m]
    print("%s -> rc=%s wall=%.1fs lines=%d  frame_lines=%d  sample_lines=%d"
          % (tag, proc.returncode, dt, len(chunks), len(frames), len(samples)))
    if samples:
        print("   first sample: %s" % samples[0].group(0))
        print("   last  sample: %s" % samples[-1].group(0))
    return log


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="重跑 Blender 渲染输出探针（大版本升级后必跑）")
    ap.add_argument("--blender", help="blender.exe 路径（默认自动探测）")
    ns = ap.parse_args()
    CURRENT_BLENDER = pick_blender(ns.blender)
    CURRENT_MAJOR = blender_major(CURRENT_BLENDER)
    print("Blender: %s  (主版本 %s)" % (CURRENT_BLENDER, CURRENT_MAJOR), flush=True)
    make_scene()
    capture("cycles", ["-b", BLEND, "-s", "1", "-e", "4", "-a"])
    capture("eevee", ["-b", BLEND, "-E", "BLENDER_EEVEE", "-s", "1", "-e", "1", "-a"])
