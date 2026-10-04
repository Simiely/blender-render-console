# -*- coding: utf-8 -*-
"""真机冒烟：用**真的 Blender 5.2** 跑通「渲染 → 杀进程 → 断点续跑」全链路。

单测（tests/test_core.py）用的是 fake_blender，验证的是接线；
这个脚本验证的是**真机行为**：bpy API 对不对、引擎/采样覆盖生效没有、
`frame_path` 算出来的路径对不对、Blender 真被杀掉之后续跑能不能接上。

    python tools/smoke_real_blender.py            # 跑全部
    python tools/smoke_real_blender.py --keep     # 保留产物目录以便查看

⚠️ 杀进程只杀**本脚本自己启动的** blender（按 PID 差集），不会误伤你正在用的 Blender。
"""

import argparse
import glob
import json
import os
import re
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
WORK = os.path.join(HERE, "_smoke")
PY = sys.executable

RE_RESTART = re.compile(r"退出码|重启 (\d+) 次|任务结束")


def log(msg):
    print(msg, flush=True)


def make_scene(path, frames=6, samples=8):
    """用 --factory-startup 造一个极简场景（不依赖任何已有 .blend）。"""
    expr = (
        "import bpy;"
        "sc=bpy.context.scene;"
        "sc.render.engine='CYCLES';"
        "sc.cycles.device='CPU';"
        "sc.cycles.samples=%d;"
        "sc.render.resolution_x=160;"
        "sc.render.resolution_y=120;"
        "sc.render.resolution_percentage=100;"
        "sc.frame_start=1;sc.frame_end=%d;"
        "sc.render.image_settings.file_format='PNG';"
        "bpy.ops.wm.save_as_mainfile(filepath=r'%s')" % (samples, frames, path)
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    p = subprocess.run([BLENDER, "-b", "--factory-startup", "--python-expr", expr],
                       capture_output=True)
    if not os.path.exists(path):
        raise SystemExit("建场景失败，rc=%s\n%s" % (p.returncode, p.stdout.decode("utf-8", "replace")[-800:]))
    return path


def done_count(state_path):
    """从断点文件读已完成帧数 —— 比数 png 更准（png 可能正在写一半）。"""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return len(json.load(f).get("done") or {})
    except Exception:
        return 0


def blender_pids():
    """当前所有 blender.exe 的 PID —— 用于只杀我们自己拉起来的那些。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq blender.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=15).stdout.decode("utf-8", "replace")
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        m = re.search(r'"blender\.exe","(\d+)"', line)
        if m:
            pids.add(int(m.group(1)))
    return pids


def kill_blenders(pids):
    for pid in sorted(pids):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def clean(d):
    """清空目录 —— 注意 glob('*') 拿不到点开头的 state 文件，必须走 listdir。"""
    if os.path.isdir(d):
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                os.remove(p)
            except OSError:
                pass


def run_cli(args, timeout=300):
    """跑 main.py，返回 (rc, stdout)。"""
    cmd = [PY, os.path.join(ROOT, "main.py")] + args
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def check(name, ok, detail=""):
    log("  %s %s%s" % ("✓" if ok else "✗", name, (" —— " + detail) if detail else ""))
    return ok


def case_plain_render(blend, out_dir):
    """场景一：完整跑一轮，6 帧全出。"""
    log("\n[1/6] 完整渲染 6 帧（Cycles / CPU / 8 samples）")
    clean(out_dir)
    t0 = time.time()
    rc, out = run_cli([blend, "-s", "1", "-e", "6", "-E", "CYCLES", "--samples", "8",
                       "--res", "160x120", "--device", "CPU",
                       "-o", os.path.join(out_dir, "smoke_####"),
                       "--log", os.path.join(out_dir, "blender.log"),
                       "--state", os.path.join(out_dir, ".state.json")])
    pngs = sorted(glob.glob(os.path.join(out_dir, "smoke_*.png")))
    ok = check("退出码 0", rc == 0, "rc=%s" % rc)
    ok &= check("产出 6 张图", len(pngs) == 6, "实际 %d 张：%s"
                % (len(pngs), [os.path.basename(p) for p in pngs][:8]))
    ok &= check("耗时合理", time.time() - t0 < 180, "%.1fs" % (time.time() - t0))
    if "任务结束" not in out:
        ok &= check("有任务结束汇总", False, out[-400:])
    return ok, out


def case_kill_and_resume(blend, out_dir):
    """场景二：渲染中途杀掉 Blender，验证会自动重启并从断点接上。"""
    log("\n[2/6] 中途杀掉 Blender，验证崩溃续跑")
    clean(out_dir)
    # 每帧要够慢（~1s），否则检测循环会一帧都抓不到中间态，等于没测到崩溃
    cmd = [PY, os.path.join(ROOT, "main.py"), blend, "-s", "1", "-e", "6",
           "-E", "CYCLES", "--samples", "24", "--res", "320x240", "--device", "CPU",
           "-o", os.path.join(out_dir, "smoke_####"),
           "--state", os.path.join(out_dir, ".state.json"),
           "--log", os.path.join(out_dir, "blender.log")]
    before = blender_pids()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    # 用 state 里的完成数判断下手时机：早了测不到「有进度可续」，晚了就全渲染完了
    deadline = time.time() + 120
    killed = 0
    state_file = os.path.join(out_dir, ".state.json")
    while time.time() < deadline:
        if 1 <= done_count(state_file) <= 5:
            new = blender_pids() - before
            if new:
                log("    杀掉 blender：%s（此时已产出 %d 张）"
                    % (sorted(new), len(glob.glob(os.path.join(out_dir, "smoke_*.png")))))
                kill_blenders(new)
                killed = 1
                time.sleep(0.3)
                alive = blender_pids() & new
                log("    kill 后仍存活：%s" % (sorted(alive) or "无"))
            else:
                log("    ⚠ 没找到本次启动的 blender 进程，无法验证崩溃续跑")
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    ok = check("确实杀到了 Blender 进程", bool(killed))
    try:
        out = proc.communicate(timeout=240)[0].decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        out = ""
        ok &= check("CLI 在超时内结束", False)

    pngs = sorted(glob.glob(os.path.join(out_dir, "smoke_*.png")))
    ok &= check("续跑后 6 帧齐全", len(pngs) == 6, "实际 %d 张" % len(pngs))
    restarted = ("退出码" in out) or ("重启" in out)
    marks = [l.strip() for l in out.splitlines() if "退出码" in l or "重启" in l]
    ok &= check("输出里有崩溃/重启痕迹", restarted, " | ".join(marks[:3]))
    for l in out.splitlines():
        if l.strip():
            log("    %s" % l.strip())
    return ok, out


def case_eevee(blend, out_dir):
    """场景三：切 EEVEE，验证引擎别名（5.2 里可能叫 BLENDER_EEVEE_NEXT）能落地。"""
    log("\n[3/6] EEVEE 单帧（验证引擎别名 + 首帧 shader 预热）")
    clean(out_dir)
    t0 = time.time()
    rc, out = run_cli([blend, "-s", "1", "-e", "2", "-E", "BLENDER_EEVEE", "--samples", "16",
                       "-o", os.path.join(out_dir, "eevee_####"),
                       "--state", os.path.join(out_dir, ".state_eevee.json"),
                       "--log", os.path.join(out_dir, "eevee.log")], timeout=300)
    pngs = sorted(glob.glob(os.path.join(out_dir, "eevee_*.png")))
    ok = check("退出码 0", rc == 0, "rc=%s" % rc)
    ok &= check("产出 2 张图", len(pngs) == 2, str([os.path.basename(p) for p in pngs]))
    engine_line = [l for l in out.splitlines() if "实际设置" in l]
    ok &= check("引擎落到 EEVEE", bool(engine_line) and "EEVEE" in engine_line[0],
                engine_line[0].strip() if engine_line else out[-300:])
    warm = [l for l in out.splitlines() if "预热帧" in l]
    ok &= check("首帧被标记为预热", len(warm) == 1, warm[0].strip() if warm else "没找到预热帧标记")
    log("    用时 %.1fs（EEVEE 首帧要编译 shader，慢是正常的）" % (time.time() - t0))
    return ok, out


def case_inspect(blend):
    """场景四：只读工程配置（不渲染）——验证 inspect.py 的 bpy 取数逻辑。"""
    log("\n[4/6] 读取工程配置（无头 Blender 读 .blend，不渲染）")
    from brconsole.inspect import output_template_from, read_blend_info
    t0 = time.time()
    info = read_blend_info(BLENDER, blend)
    ok = check("读取成功", bool(info.get("ok")), str(info.get("error", "")))
    if not info.get("ok"):
        return ok, ""
    ok &= check("引擎 = CYCLES", info.get("engine") == "CYCLES", str(info.get("engine")))
    ok &= check("采样 = 8", info.get("samples") == 8, str(info.get("samples")))
    ok &= check("分辨率 = 160x120", info.get("resolution") == [160, 120],
                str(info.get("resolution")))
    ok &= check("帧范围 = 1-6",
                (info.get("frame_start"), info.get("frame_end")) == (1, 6),
                "%s-%s" % (info.get("frame_start"), info.get("frame_end")))
    ok &= check("设备 = CPU", (info.get("cycles_device") or "").upper() == "CPU",
                str(info.get("cycles_device")))
    tpl = output_template_from(info.get("output_path"), blend)
    ok &= check("输出模板可用", "####" in tpl, tpl)
    log("    用时 %.1fs（读一次要起一个无头 Blender）" % (time.time() - t0))
    return ok, ""


MULTI_SCENE_SCRIPT = r'''
import sys
import bpy

path = sys.argv[sys.argv.index("--") + 1]


def setup(sc, res, frames, filepath):
    cam_data = bpy.data.cameras.new(sc.name + "_cam")
    cam = bpy.data.objects.new(sc.name + "_cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam                 # 没有相机的场景 Blender 直接拒绝渲染
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.frame_start, sc.frame_end = frames
    sc.render.filepath = filepath


a = bpy.data.scenes[0]              # 最后一个场景删不掉，所以是给默认场景改名
a.name = "SceneA"
setup(a, (64, 48), (1, 2), "//outA/frame_")

b = bpy.data.scenes.new("SceneB")
setup(b, (128, 96), (10, 11), "//outB/shot_")

bpy.ops.wm.save_as_mainfile(filepath=path)
print("MULTI-SCENE-SAVED", path)
'''


def make_multi_scene(path):
    """造一个**双场景**工程：SceneA=64x48、SceneB=128x96，各带一个相机。

    激活场景保持默认的 SceneA —— 这样"指定了一个不存在的场景名"时，能靠产物尺寸
    看出 Blender 是不是偷偷回落到了 SceneA。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    script = os.path.join(WORK, "_mk_multi_scene.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(MULTI_SCENE_SCRIPT)
    p = subprocess.run([BLENDER, "-b", "--factory-startup", "-P", script, "--", path],
                       capture_output=True)
    if not os.path.exists(path):
        raise SystemExit("建多场景工程失败，rc=%s\n%s"
                         % (p.returncode,
                            p.stdout.decode("utf-8", "replace")[-800:]))
    return path


def png_size(path):
    """读 PNG 头里的宽高 —— 用来核对"渲的是哪个场景"，不引第三方图像库。"""
    try:
        with open(path, "rb") as f:
            head = f.read(33)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def case_multi_scene(blend, out_dir):
    """场景六：多场景直选。

    两个场景的分辨率刻意不同（64x48 / 128x96），所以**产物尺寸就是"渲的是哪个场景"
    的硬证据**，比看日志可靠。另单验一条：场景名写错时 Blender 会静默回落到默认场景
    （退出码仍是 0），我们必须有明确告警，不能让它悄悄过去。

    ⚠️ 这一项**不能**传 `--res`：一覆盖分辨率，两个场景就分不出来了。
    """
    log("\n[6/6] 多场景：-S 选场景（含场景名写错时的告警与回落）")
    ok = True
    for name, want in (("SceneA", (64, 48)), ("SceneB", (128, 96))):
        d = os.path.join(out_dir, name.lower())
        clean(d)
        os.makedirs(d, exist_ok=True)
        rc, out = run_cli([blend, "-S", name, "-f", "1", "--blender", BLENDER,
                           "-o", os.path.join(d, "s_####"),
                           "--state", os.path.join(d, ".state.json"),
                           "--no-resume"], timeout=300)
        pngs = sorted(glob.glob(os.path.join(d, "s_*.png")))
        got = png_size(pngs[0]) if pngs else None
        log("  [%s] rc=%s | 产物 %s" % (name, rc, got))
        ok &= check("  %s：渲出 %dx%d" % (name, want[0], want[1]), got == want,
                    "实际 %s，%d 张" % (got, len(pngs)))
        ok &= check("  %s：命令行回显了场景名" % name, ("场景 %s" % name) in out)

    d = os.path.join(out_dir, "badname")
    clean(d)
    os.makedirs(d, exist_ok=True)
    rc, out = run_cli([blend, "-S", "NoSuchScene", "-f", "1", "--blender", BLENDER,
                       "-o", os.path.join(d, "s_####"),
                       "--state", os.path.join(d, ".state.json"),
                       "--no-resume"], timeout=300)
    pngs = sorted(glob.glob(os.path.join(d, "s_*.png")))
    got = png_size(pngs[0]) if pngs else None
    log("  [NoSuchScene] rc=%s | 产物 %s（默认场景 SceneA = 64x48）" % (rc, got))
    ok &= check("  场景名写错：有明确告警", "没有场景" in out)
    ok &= check("  场景名写错：回落到默认场景并照常渲完", got == (64, 48), str(got))
    return ok, ""


CANARY_FAILS = 6        # 故意大于默认上限 5 —— 这样两个分支的结果才会分叉


def make_canary(path, blender, fails=CANARY_FAILS):
    """造一个「前 fails 次启动直接失败，之后原样转交真 Blender」的 .bat 启动器。

    为什么不用"外部 taskkill"来制造崩溃：那依赖时序 —— blender 什么时候渲完、
    什么时候被杀，每次跑都不一样，实测崩溃次数在 5~7 之间浮动，断言必然时灵时不灵。
    而"一直重启"要验证的恰恰是**次数**，所以把"崩"变成确定事件；真渲染仍由真 Blender 完成。

    （外部 taskkill 的真实场景另有 [2/6] 覆盖，两者互补。）
    """
    # ⚠️ 这里用了 % 风格格式化，所以批处理自己的 % 全部要写成 %% 转义；
    # ⚠️ 转交路径必须是反斜杠 —— cmd.exe 不认正斜杠，写成 `C:/...exe` 会找不到而返回 1，
    #    那样"永远崩"和"该崩几次"就分不清了（踩过一次）。
    blender_native = blender.replace("/", "\\")
    bat = (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "F=%%~dp0runs.txt"\r\n'
        "set N=0\r\n"
        # 用 for /f 读计数器而不是 set /p：set /p 会把行尾的 CR 一起吃进来，
        # 之后 `set /a N=%N%+1` 直接算错（实测计数永远停在 1，四次要崩的变成"全都崩"）
        'if exist "%%F%%" for /f "usebackq delims=" %%%%i in ("%%F%%") do set N=%%%%i\r\n'
        "set /a N=%%N%%+1\r\n"
        '> "%%F%%" echo %%N%%\r\n'
        "if %%N%% LEQ %d exit /b 1\r\n"
        '"%s" %%*\r\n' % (fails, blender_native)
    )
    with open(path, "w", encoding="ascii", newline="") as f:
        f.write(bat)
    return path


def case_unlimited_restart(blend12, out_dir, fails=CANARY_FAILS):
    """场景五：「一直重启」的 A/B 对照 —— 同样的连续崩溃，不同旋钮，结果必须分叉。

    A：`--max-restarts 5`（默认上限）→ 必须放弃，一帧都渲不出来
    B：`--max-restarts unlimited`   → 必须撑过全部崩溃，把 12 帧渲完

    两侧都关掉 no-progress 护栏（`--max-no-progress 0`），否则护栏会先于重启上限生效，
    分不清到底是谁让任务停下的 —— 少一个变量的对照才说明问题。
    """
    log("\n[5/6] 「一直重启」A/B 对照：同一个「连崩 %d 次」启动器，两种上限" % fails)
    ok = True

    for tag, limit, expect_giveup in (("A 上限 5 次", "5", True),
                                      ("B 不限次数", "unlimited", False)):
        d = os.path.join(out_dir, tag[:1])
        clean(d)
        os.makedirs(d, exist_ok=True)
        canary = make_canary(os.path.join(d, "canary.bat"), BLENDER, fails)
        rc, out = run_cli([blend12, "-s", "1", "-e", "12",
                           "-E", "CYCLES", "--samples", "8", "--res", "160x120",
                           "--device", "CPU", "--blender", canary,
                           "--max-restarts", limit, "--max-no-progress", "0",
                           "-o", os.path.join(d, "smoke_####"),
                           "--state", os.path.join(d, ".state.json"),
                           "--log", os.path.join(d, "blender.log")], timeout=600)
        pngs = sorted(glob.glob(os.path.join(d, "smoke_*.png")))
        m = re.search(r"重启 (\d+) 次", out)
        restarts = int(m.group(1)) if m else -1
        log("  [%s] 重启 %s 次 | 产出 %d 张 | rc=%s" % (tag, restarts, len(pngs), rc))

        if expect_giveup:
            ok &= check("  A：因「已达上限」停下", "已达上限" in out)
            ok &= check("  A：一帧都没渲出来（上限确实咬住了）", len(pngs) == 0,
                        "实际 %d 张" % len(pngs))
        else:
            ok &= check("  B：没有出现「已达上限」", "已达上限" not in out)
            ok &= check("  B：启动时声明了「重启次数不限」", "重启次数不限" in out)
            ok &= check("  B：重启 %d 次（已超过默认上限 5）" % fails, restarts >= fails,
                        "实际 %s 次" % restarts)
            ok &= check("  B：12 帧全部渲完", len(pngs) == 12, "实际 %d 张" % len(pngs))
            ok &= check("  B：汇总为 完成 12/12", "完成 12/12" in out,
                        " | ".join(l.strip() for l in out.splitlines()
                                   if "任务结束" in l)[:160])
        for l in out.splitlines():
            if any(k in l for k in ("任务开始", "重启", "任务结束", "已达上限", "无进展")):
                log("      %s" % l.strip())
    return ok, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留产物目录")
    ap.add_argument("--only", choices=["1", "2", "3", "4", "5", "6"], help="只跑某一个场景")
    args = ap.parse_args()

    if not os.path.exists(BLENDER):
        raise SystemExit("找不到 Blender：%s（改脚本里的 BLENDER 常量）" % BLENDER)

    os.makedirs(WORK, exist_ok=True)
    blend = make_scene(os.path.join(WORK, "smoke.blend"))
    blend12 = make_scene(os.path.join(WORK, "smoke12.blend"), frames=12)
    blend_multi = make_multi_scene(os.path.join(WORK, "multi.blend"))
    log("场景文件：%s / %s / %s" % (blend, blend12, blend_multi))

    results = []
    try:
        if args.only in (None, "1"):
            results.append(case_plain_render(blend, os.path.join(WORK, "out1")))
        if args.only in (None, "2"):
            results.append(case_kill_and_resume(blend, os.path.join(WORK, "out2")))
        if args.only in (None, "3"):
            results.append(case_eevee(blend, os.path.join(WORK, "out3")))
        if args.only in (None, "4"):
            results.append(case_inspect(blend))
        if args.only in (None, "5"):
            results.append(case_unlimited_restart(blend12, os.path.join(WORK, "out5")))
        if args.only in (None, "6"):
            results.append(case_multi_scene(blend_multi, os.path.join(WORK, "out6")))
    finally:
        if not args.keep:
            try:
                import shutil
                shutil.rmtree(WORK)
            except Exception as e:
                log("（清理失败：%r，产物留在 %s）" % (e, WORK))

    all_ok = all(r[0] for r in results)
    log("\n===== %s =====" % ("真机冒烟全部通过" if all_ok else "真机冒烟存在失败项"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
