# -*- coding: utf-8 -*-
"""假 Blender：给 core 的续跑逻辑做端到端测试用。

不启动真 Blender（太慢且依赖 GPU），但输出**与真机同构**：
Blender 原生风格的行 + `##PROG##{JSON}` 进度行，并且可以在指定帧「崩掉」。

用法（由 tests/run.py 拼命令）：
    python fake_blender.py --job job.json --fail-at 3 [--exit-code 3]
                           [--no-native] [--sleep S] [--eof-early]
"""

import argparse
import json
import os
import sys
import time


def emit(kind, **kw):
    kw["kind"] = kind
    kw["t"] = round(time.time(), 3)
    sys.stdout.write("##PROG##" + json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def native(text):
    """模仿 5.2 的行格式：`<秒表>  render | <内容>`，结尾用 \\r 刷新（和真机一致）"""
    sys.stdout.write("00:01.%03d  render           | %s\r" % (int(time.time() * 1000) % 1000, text))
    sys.stdout.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--job", required=True)
    p.add_argument("--fail-at", type=int, default=0, help="渲染到该帧时崩溃（0=不崩）")
    p.add_argument("--exit-code", type=int, default=3)
    p.add_argument("--fail-first-run-only", action="store_true",
                   help="只在第一次启动时崩（靠 job.json 旁的 .fake_run_count 计次）")
    p.add_argument("--fail-runs", type=int, default=0,
                   help="前 N 次启动都在 --fail-at 帧崩，之后正常（测「一直重启直到完成」用）")
    p.add_argument("--sleep", type=float, default=0.05, help="每帧假装渲染多久")
    p.add_argument("--no-native", action="store_true", help="不吐原生行")
    p.add_argument("--eof-early", action="store_true", help="不发 all_done 就退出（模拟被杀）")
    args = p.parse_args()

    with open(args.job, "r", encoding="utf-8") as f:
        job = json.load(f)
    frames = [int(x) for x in job["frames"]]
    tpl = job["output_template"]
    ext = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr"}.get(job.get("file_format"), ".png")

    if not args.no_native:
        native("Read blend: \"%s\"" % job.get("blend", "x.blend"))
        native("Fra: %d | Mem: 0M | Synchronizing object | Cube" % frames[0])
        native("Fra: %d | Mem: 1M | Loading render kernels (may take a few minutes the first time)" % frames[0])
        native("Fra: %d | Mem: 1M | Updating Scene BVH | Building" % frames[0])

    emit("start", frames=frames, blend=job.get("blend", "x.blend"), engine="CYCLES",
         scene=job.get("scene") or "Scene", scene_requested=job.get("scene"))
    emit("info", engine="CYCLES", res=[320, 240], pct=100, output=tpl, samples=24)

    # 进程启动计次：core 每重启一轮就一个新进程，靠它区分「第一次」和「续跑」
    counter = os.path.join(os.path.dirname(os.path.abspath(args.job)), ".fake_run_count")
    try:
        with open(counter, "r", encoding="utf-8") as fp:
            run_index = int(fp.read().strip() or 0) + 1
    except (OSError, ValueError):
        run_index = 1
    try:
        with open(counter, "w", encoding="utf-8") as fp:
            fp.write(str(run_index))
    except OSError:
        pass

    for i, f in enumerate(frames):
        path = tpl.replace("####", "%04d" % f) + ext
        emit("frame_start", frame=f, index=i, total=len(frames), path=path)
        time.sleep(args.sleep)
        if args.fail_at and f == args.fail_at:
            crash_now = run_index <= 1 if args.fail_first_run_only else True
            if args.fail_runs:
                crash_now = run_index <= args.fail_runs
            if crash_now:
                native("Fra: %d | Mem: 6M | Sample 12/24" % f)
                sys.stdout.flush()
                sys.exit(args.exit_code)
        for s in (6, 12, 24):
            native("Fra: %d | Mem: 6M | Sample %d/24" % (f, s))
        # 真的落一个文件，让「已完成」可验证
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "wb") as fp:
            fp.write(b"\x89PNG\r\n\x1a\nFAKE")
        native("Saved: '%s'" % path)
        emit("frame_done", frame=f, index=i, total=len(frames), secs=args.sleep, path=path)

    if not args.eof_early:
        emit("all_done", n=len(frames), ok=len(frames), failed=0)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
