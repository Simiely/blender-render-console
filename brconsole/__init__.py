# -*- coding: utf-8 -*-
"""blender-render-console · 主程序包。

分层（自下而上）。**I 值、扇入榜、SDP 违规数不在这里写死** ——
改一次依赖就过期一次，准数只认 `python tools/arch_metrics.py` 的实测输出：

    底层（改它们代价最大；谁排第一同样看脚本的扇入榜）
      parser.py     Blender 原生输出行解析
      eta.py        ETA 估算（剔除首帧预热 + EMA 平滑）
      state.py      断点状态文件（原子落盘）
      layout.py     打包布局常量
      locate.py     blender.exe 探测（文件系统扫描，不用注册表）
      diskspace.py  开工前的磁盘空间预检
      autostart.py  开机自启（HKCU 的 Run 项）
      theme.py      深色主题
      uikit.py      可复用 UI 零件（卡片 / 按钮 / 胶囊 / 滚动区）
      tkboot.py     缺 tkinter 时带 sidecar 自举重启
    （碰 tkinter 的都在界面层：gui/ 包、gui_view_new、ui_*、theme、uikit；
     白名单由 `tools/check_boundaries.py` 守着）

    core.py         调度核心：子进程 + 双通道读取 + 崩溃续跑
                    （帧范围解析 `parse_frames` 是共用工具，放在这里不许界面找 cli 借）
    inspect.py      读取 .blend 里已有的渲染配置（另起一个 Blender 进程）
    guimodel/       界面纯逻辑包：constants / frames / form / events / progress /
                    logs / stateinfo；`__init__.py` 只是兼容门面（不碰 tkinter，可单测）
    taskstore.py    待办任务存档（开机续跑的依据：任务"是什么"；autoresume 唯一来源）
    queue_store.py  多工程队列（顺序与状态；不再存第二份 autoresume）
    cli.py          命令行适配器 —— 只该被组合根依赖，不许被界面依赖
    view_contract.py 控制器与视图之间的语义契约（无 tkinter，FakeView 可单测）
    gui/            界面控制包：app 薄壳 + app_state + 8 个 controller mixin +
                    common（共享导入）；唯一 Tk 视图实现在 gui_view_new.TkView
    driver.py       跑在 **Blender 进程内**的驱动脚本。它不是本机的模块，
                    靠 `core.read_driver_source()` 注入源码字符串执行，
                    所以依赖图上没有任何边 —— 改了它要跑真机冒烟。

**组合根是 `main.py`**：只有它可以把上面这些拼起来（它天然"什么都知道"，
所以它 import 界面层不算违规）。依赖方向必须指向更稳定的方向（SDP）。
实测 0 环、`tools/check_boundaries.py` 的 7 条规则全过；SDP 的实测违规数以脚本为准。
⚠️ `guimodel/__init__.py` 与 `gui/` 内 controller 经 `common` 共享导入会产生 SDP 违规：
前者是兼容门面，后者是同层共享导入，都不是领域层耦合反了；判断架构仍以 0 环与边界规则为准。
"""

# ⚠️ 这个文件**只放文档和版本号，绝不 import 子模块、也不放共用常量**。
#    理由有两条，都是实测踩过的：
#    ① 一旦在这里 import 子模块，而子模块又要从包上取东西，就会撞上"还没执行到那行"的
#       包 → `ImportError: cannot import name ... from partially initialized module`
#       （PEP8 还建议把 import 写在文件顶部，正好是最容易炸的位置，2026-10-05 复现过）。
#    ② 在这里放共用常量会让包根变成"谁都要来取一句"的汇聚点，把①的雷埋起来。
#    共用常量放**叶子模块**：打包布局见 `layout.py`。
#    这条由 `tools/check_boundaries.py` 的 `init-stays-leaf` 规则守着。

__version__ = "0.11.0"
