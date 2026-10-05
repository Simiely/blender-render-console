# -*- coding: utf-8 -*-
"""打包后的目录布局 —— 一个**叶子模块**，只有常量、不 import 任何东西。

为什么专门开一个模块放这一句 `"_brc"`：

1. **单一来源**。这句曾经在 5 个文件里各写了一份（tkboot / core / main / gui /
   tools/build_exe.py），其中一处靠注释「与 tkboot.FROZEN_SUBDIR 保持一致」维持。
   那种一致性断掉之后的现象是**打包后功能静默缺失**（自检看得见，人手不一定看得见）。
2. **不能再放回 `brconsole/__init__.py`**。放那儿一开始能跑，但 `__init__` 会立刻变成
   「谁都要来取一句常量」的汇聚点；哪天有人图方便在里面加一句
   `from .core import RenderJob`（PEP8 还会建议把 import 写在文件顶部），
   core → `from .layout import ...` 这条链就会撞上**还没执行到常量定义**的包，
   报 `ImportError: cannot import name ... from partially initialized module`。
   2026-10-05 实测复现过，两条路（定义前 / 定义后）只有前一条会炸，而前一条恰恰是
   PEP8 推荐的位置。
   放成本叶子模块后，这个雷**从根上不存在**，而不是"靠一条 lint 规则提醒别踩"。
3. 依赖方向上它是**最底层**：谁都依赖它、它不依赖任何人（I = 0.00），
   与 `eta.fmt_duration` 那类共用工具的位置是同一个道理。

改这里的名字 = 同时改 `tools/build_exe.py`（往哪儿塞数据文件）与运行时（从哪儿挂回来），
所以只有一处可改是它的全部意义。
"""

# 打包后所有内置数据的根目录名，即 `<_MEIPASS>/_brc/`：
#   DLLs/  Lib/tkinter/  tcl/      —— tkboot 挂回来给 GUI 用
#   py/driver.py                   —— core.read_driver_source 注入给 Blender
#   assets/app.ico                 —— gui.icon_path
#   selftest/fake_blender.py       —— main 的 --demo / 打包自检
FROZEN_SUBDIR = "_brc"
