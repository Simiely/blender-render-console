# -*- coding: utf-8 -*-
"""blender-render-console · 主程序包。

分层（与 DEVELOPMENT.md 的架构图一致）：

    cli.py     命令行入口 / 界面消费方
    core.py    调度核心：子进程 + 双通道读取 + 崩溃续跑
    driver.py  运行在 Blender 进程内的驱动脚本（不是本机的模块，靠源码字符串注入）
    parser.py  Blender 原生输出行解析
    eta.py     ETA 估算（剔除首帧预热 + EMA 平滑）
    state.py   断点状态文件（原子落盘）
    taskstore.py 待办任务存档（开机续跑的依据：任务"是什么"）
    autostart.py 开机自启（HKCU 的 Run 项）
    locate.py  blender.exe 探测（文件系统扫描，不用注册表）
"""

__version__ = "0.6.1"
