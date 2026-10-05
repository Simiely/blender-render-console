# -*- coding: utf-8 -*-
"""blender-render-console 入口（PyInstaller 打包入口也是它）。

    python main.py                           # 无参数 → 图形界面
    python main.py --gui 工程.blend           # 图形界面，并直接载入该工程（自动读配置）
    python main.py --autostart               # 开机自启用：认领未完成任务并自动续跑
    python main.py --demo                    # 图形界面 + 自动跑一轮模拟任务（自检用；
                                             #   打包后的 exe 同样支持，用的是 exe 内置的假 Blender）
    python main.py 工程.blend -s 1 -e 240      # 命令行渲染
    python main.py --help                    # 命令行选项清单
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from brconsole import FROZEN_SUBDIR, tkboot  # noqa: E402
from brconsole.cli import main as cli_main  # noqa: E402

GUI_FLAGS = {"--gui", "--demo", "--autostart"}
FAKE_FLAG = "--fake-blender"      # 打包后 --demo 用它把本 exe 当「假 Blender」再拉起来
SELFTEST_SUBDIR = (FROZEN_SUBDIR, "selftest")

# 打包后：先把 exe 自带的 tcl/tk 挂上，再谈别的（必须在任何 import tkinter 之前）
tkboot.apply_frozen_env()


def _fake_blender_script():
    """定位假 Blender：源码跑用 tests/fake_blender.py，打包后用 exe 内那份副本。"""
    src = os.path.join(ROOT, "tests", "fake_blender.py")
    if os.path.exists(src):
        return src
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, *SELFTEST_SUBDIR, "fake_blender.py")
        if os.path.exists(p):
            return p
    return None


def run_fake_blender(argv):
    """以「内置假 Blender」身份跑一次自己（`--demo` 自检专用，不是用户功能）。

    为什么要绕这一圈：打包后没有独立的 python.exe 可以拉起来跑 tests/fake_blender.py，
    而自检又必须走**真实的多进程**路径才测得出接线问题，所以让 exe 自己再当一次子进程。
    """
    import importlib.util
    argv = list(argv)
    if "--job" not in argv and argv:
        # 兼容 core 的调用形式：`<exe> -b 工程 -P driver --python-exit-code 1 ... -- <job.json>`，
        # 即 job 路径是 `--` 之后的最后一个参数。这样打包后自检就能走**完整 core 流水线**
        # （驱动脚本注入 + 进程管理 + 双通道解析 + 断点文件），而不只是单跑一次假 Blender。
        argv = ["--job", argv[-1]]
    script = _fake_blender_script()
    if script is None:
        sys.stderr.write("内置假 Blender 缺失（打包时没带上 tests/fake_blender.py）\n")
        return 3
    spec = importlib.util.spec_from_file_location("brc_fake_blender", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    saved = sys.argv
    sys.argv = [script] + list(argv)
    try:
        rc = mod.main()
        return 0 if rc is None else rc
    finally:
        sys.argv = saved


def _demo_cmd_factory():
    """--demo 专用：把 Blender 换成仓库内的假 Blender（不走真机、不吃 GPU）。"""
    script = _fake_blender_script()
    if script is None:
        return None

    def factory(blender_exe, blend, driver_path, job_json, extra_args=()):
        if getattr(sys, "frozen", False):
            head = [sys.executable, FAKE_FLAG]
        else:
            head = [sys.executable, script]
        # 每帧 0.4s：够慢，人工/截图都能看到"进行中"的样子
        return head + ["--job", job_json, "--sleep", "0.4"] + list(extra_args)

    return factory


def run_gui(argv):
    # ⚠️ 顺序不能反：必须先自举（可能带 sidecar 重启进程）再 import gui，
    # 否则 `import tkinter` 在这一步就炸了，永远轮不到自举。
    if not tkboot.bootstrap(ROOT, argv) and not tkboot.has_tkinter():
        sys.stderr.write(
            "本机 Python 没有 tkinter，也没有可用的 sidecar。\n"
            "先执行：python tools/build_tkinter.py\n")
        return 2
    from brconsole.gui import run_gui as _run

    demo = "--demo" in argv
    autostart_mode = "--autostart" in argv
    rest = [a for a in argv if a not in GUI_FLAGS]     # --gui <工程.blend>
    preset = rest[0] if rest else None
    factory = _demo_cmd_factory() if demo else None
    if demo and factory is None:
        sys.stderr.write("--demo 需要 tests/fake_blender.py（打包版应随 exe 一起带上）\n")
        return 2
    return _run(demo=demo, cmd_factory=factory, preset_blend=preset,
                autostart_mode=autostart_mode)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == FAKE_FLAG:
        return run_fake_blender(argv[1:])
    if not argv or argv[0] in GUI_FLAGS:
        return run_gui(argv)
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
