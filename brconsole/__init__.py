# -*- coding: utf-8 -*-
"""blender-render-console · 主程序包。

分层（自下而上；括号里的 I 是实测耦合不稳定性，`python tools/arch_metrics.py` 可复现）：

    底层 —— I = 0.00，谁都可以依赖，所以改它们代价最大
      parser.py     Blender 原生输出行解析
      eta.py        ETA 估算（剔除首帧预热 + EMA 平滑）
      state.py      断点状态文件（原子落盘）
      locate.py     blender.exe 探测（文件系统扫描，不用注册表）
      diskspace.py  开工前的磁盘空间预检
      autostart.py  开机自启（HKCU 的 Run 项）
      theme.py      深色主题（和 gui 一起，是全仓仅有的两个碰 tkinter 的文件）
      tkboot.py     缺 tkinter 时带 sidecar 自举重启

    core.py         I = 0.38  调度核心：子进程 + 双通道读取 + 崩溃续跑（**扇入第一**）
    inspect.py      I = 0.50  读取 .blend 里已有的渲染配置
    cli.py          I = 0.60  命令行适配器 —— **只该被组合根依赖，不许被界面依赖**
                              （帧范围解析 `parse_frames` 是共用工具，放在 core 里）
    guimodel.py     I = 0.75  界面逻辑：表单校验 / 事件→状态 / 日志缓冲（不碰 tkinter，可单测）
    taskstore.py    I = 0.75  待办任务存档（开机续跑的依据：任务"是什么"）
    gui.py          I = 1.00  tkinter 界面：控件 + 后台线程 + 事件队列
    driver.py       ——       跑在 **Blender 进程内**的驱动脚本。它不是本机的模块，
                              靠 `core.read_driver_source()` 注入源码字符串执行，
                              所以依赖图上没有任何边 —— 改了它要跑真机冒烟。

**组合根是 `main.py`**：只有它可以把上面这些拼起来（它天然"什么都知道"，
所以它 import 界面层不算违规）。依赖方向必须指向更稳定的方向（SDP），
实测 0 环、0 违规；这条由 `tools/check_boundaries.py` 守着。
"""

# 打包后所有内置数据的根目录名。**单一来源** ——
# tkboot（把 tcl/tk 从哪儿挂回来）、core（去哪儿找 driver 源码）、
# tools/build_exe.py（往哪儿塞）都从这里取。
# 历史上这句 "_brc" **在 5 处各写了一份**（tkboot / core / main / gui / build_exe），
# 其中一处靠注释「与 tkboot.FROZEN_SUBDIR 保持一致」维持 —— 那种一致性迟早会断，
# 而且断了的现象是"打包后功能静默缺失"（自检看得见，人手不一定看得见）。
FROZEN_SUBDIR = "_brc"

__version__ = "0.6.3"
