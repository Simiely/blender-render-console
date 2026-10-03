# -*- coding: utf-8 -*-
"""blender-render-console 入口（PyInstaller 打包入口也是它）。

    python main.py                           # 无参数 → 图形界面
    python main.py --gui 工程.blend           # 图形界面，并直接载入该工程（自动读配置）
    python main.py --demo                    # 图形界面 + 自动跑一轮模拟任务（自检用）
    python main.py 工程.blend -s 1 -e 240      # 命令行渲染
    python main.py --help                    # 命令行选项清单
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from brconsole import tkboot  # noqa: E402
from brconsole.cli import main as cli_main  # noqa: E402

GUI_FLAGS = {"--gui", "--demo"}


def _demo_cmd_factory():
    """--demo 专用：把 Blender 换成仓库内的假 Blender（不走真机、不吃 GPU）。"""
    fake = os.path.join(ROOT, "tests", "fake_blender.py")

    def factory(blender_exe, blend, driver_path, job_json, extra_args=()):
        # 每帧 0.4s：够慢，人工/截图都能看到"进行中"的样子
        return ([sys.executable, fake, "--job", job_json, "--sleep", "0.4"]
                + list(extra_args))

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
    rest = [a for a in argv if a not in GUI_FLAGS]     # --gui <工程.blend>
    preset = rest[0] if rest else None
    return _run(demo=demo, cmd_factory=_demo_cmd_factory() if demo else None,
                preset_blend=preset)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in GUI_FLAGS:
        return run_gui(argv)
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
