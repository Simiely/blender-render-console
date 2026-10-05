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

# ⚠️ 这个文件**只放文档和版本号，绝不 import 子模块、也不放共用常量**。
#    理由有两条，都是实测踩过的：
#    ① 一旦在这里 import 子模块，而子模块又要从包上取东西，就会撞上"还没执行到那行"的
#       包 → `ImportError: cannot import name ... from partially initialized module`
#       （PEP8 还建议把 import 写在文件顶部，正好是最容易炸的位置，2026-10-05 复现过）。
#    ② 在这里放共用常量会让包根变成"谁都要来取一句"的汇聚点，把①的雷埋起来。
#    共用常量放**叶子模块**：打包布局见 `layout.py`。
#    这条由 `tools/check_boundaries.py` 的 `init-stays-leaf` 规则守着。

__version__ = "0.6.5"
