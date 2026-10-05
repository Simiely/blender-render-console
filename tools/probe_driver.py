# -*- coding: utf-8 -*-
"""
验证「驱动脚本」方案：
在 Blender 内逐帧渲染，每帧吐一行 JSON 进度；外层验证这些行是否**实时**可读
（核心风险：-b 模式下 PIPE 里的 Python stdout 是否被块缓冲吞住）
"""
import os
import subprocess
import time

BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
BLEND = os.path.join(HERE, "probe.blend")

DRIVER = r'''
import bpy, sys, json, time, os

def emit(kind, **kw):
    kw["t"] = round(time.time(), 3)
    kw["kind"] = kind
    sys.stdout.write("##PROG##" + json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()          # ★ 关键：不做 flush 会被块缓冲吞住

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_tmpl = argv[0]
frames = [int(x) for x in argv[1].split(",")]

sc = bpy.context.scene
emit("start", frames=frames, engine=sc.render.engine,
     res=[sc.render.resolution_x, sc.render.resolution_y],
     samples=getattr(sc.cycles, "samples", None) if sc.render.engine == "CYCLES" else None,
     blend=bpy.data.filepath)

for i, f in enumerate(frames):
    sc.frame_set(f)
    sc.render.filepath = out_tmpl.replace("####", "%04d" % f)
    t0 = time.time()
    emit("frame_start", frame=f, index=i)
    try:
        bpy.ops.render.render(write_still=True)
        emit("frame_done", frame=f, index=i, secs=round(time.time() - t0, 3),
             path=sc.render.filepath + ".png")
    except Exception as e:
        emit("frame_error", frame=f, index=i, err=repr(e))

emit("all_done", n=len(frames))
'''

drv_path = os.path.join(HERE, "probe_driver.py")
with open(drv_path, "w", encoding="utf-8", newline="\n") as f:
    f.write(DRIVER)

out_tmpl = os.path.join(HERE, "drv_####")
cmd = [BLENDER, "-b", BLEND, "-P", drv_path, "--", out_tmpl, "1,2,3"]

t0 = time.time()
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
buf = b""
events = []
for raw in iter(proc.stdout.readline, b""):
    line = raw.decode("utf-8", "replace").rstrip()
    if "##PROG##" in line:
        events.append((round(time.time() - t0, 3), line.split("##PROG##", 1)[1]))
proc.wait()

print("rc=%s wall=%.2fs  json_events=%d" % (proc.returncode, time.time() - t0, len(events)))
for t, e in events:
    print("[%7.3fs] %s" % (t, e))
