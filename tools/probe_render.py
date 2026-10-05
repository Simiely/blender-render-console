# -*- coding: utf-8 -*-
"""
实测探针：验证 1) 能否从 Blender stdout 实时读到渲染进度  2) EEVEE 在 -b 下能否渲染
产物：probe_cycles.log / probe_eevee.log（把 \r 拆成 \n，便于阅读）
"""
import os
import re
import subprocess
import time

BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
BLEND = os.path.join(HERE, "probe.blend")


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
    p = subprocess.run([BLENDER, "-b", "--factory-startup", "--python-expr", expr],
                       capture_output=True)
    print("make_scene rc=%s" % p.returncode)


def capture(tag, args, timeout=900):
    """逐块读取 Blender 的 stdout/stderr，把 \\r 拆成 \\n 后落盘，并统计耗时"""
    log = os.path.join(HERE, "probe_%s.log" % tag)
    cmd = [BLENDER] + args
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

    # 立即打印关键统计
    pat = re.compile(r"Fra:(\d+).*?\| Time:([\d:.]+)(?:.*?Remaining:([\d:.]+))?")
    hits = [m for _, l in chunks for m in [pat.search(l)] if m]
    print("%s -> rc=%s wall=%.1fs lines=%d  progress_lines=%d"
          % (tag, proc.returncode, dt, len(chunks), len(hits)))
    if hits:
        print("   first: %s" % hits[0].group(0))
        print("   last : %s" % hits[-1].group(0))
        print("   has Remaining: %s" % any(m.group(3) for m in hits))
    return log


if __name__ == "__main__":
    make_scene()
    capture("cycles", ["-b", BLEND, "-s", "1", "-e", "4", "-a"])
    capture("eevee", ["-b", BLEND, "-E", "BLENDER_EEVEE", "-s", "1", "-e", "1", "-a"])
