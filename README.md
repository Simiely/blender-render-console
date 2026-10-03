# blender-render-console

用代码（而不是 Blender GUI）启动渲染的 Windows 小工具：无头调用 Blender，实时显示**进度**与**预计结束时间**，崩溃后**自动续跑**。

---

## 为什么需要它

用 Blender GUI 渲染大型工程时容易整个进程崩掉，最典型的是 Windows 弹出「显示驱动已停止响应，并且已恢复」。

根因是 **Windows TDR（Timeout Detection and Recovery）**：GUI 模式下 GPU 既要驱动桌面、又要跑渲染，只要有一帧超过约 2 秒没能响应，Windows 就会强制重置显卡驱动，Blender 随之崩溃。

改用命令行无头渲染（`blender -b`）后，GPU 不再承担显示输出——等于把渲染从「桌面会话里的一条支线」变成「独占 GPU 的独立进程」。这是稳定性差异的主要来源，另外还顺带规避了 VRAM 被界面挤占、Persistent Data 跨帧累积、视口上下文抢 GPU、插件 hook 渲染流程等问题。

---

## 当前状态

> **调研与验证阶段已完成，主程序尚未编写。** 详细结论见 [DEVELOPMENT.md](./DEVELOPMENT.md)。

已经验证通过的部分：

| 验证项 | 结论 |
|---|---|
| Blender 5.2 命令行渲染的真实输出格式 | 与网上流传的 4.x 解析代码**完全不同**，旧正则全部失效 |
| EEVEE 能否在 `-b` 无头模式渲染 | ✅ 可以，无 GPU 报错 |
| 驱动脚本 + 结构化进度回传 | ✅ `sys.stdout.flush()` 后可实时到达，时间戳与真实耗时吻合 |
| 本机 Python 有无 tkinter | ❌ 没有，已打通从官方安装包提取 tcl/tk 的路线 |

---

## 环境（本机实测）

| 项 | 值 |
|---|---|
| Blender | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`（5.2.2 LTS，内置 Python 3.13.13） |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER，16376 MiB，驱动 616.92 |
| 托管 Python | 3.13.12（`~/.workbuddy/binaries/python/versions/3.13.12`），**无 tkinter** |
| 7-Zip | `C:\Program Files\7-Zip\7z.exe` |
| PyPI | 走腾讯云镜像可直连；Clash 代理反而超时 |

---

## 目录结构

```
.
├── tools/
│   ├── build_tkinter.py     # 从官方安装包提取并拼装 tkinter sidecar
│   ├── probe_render.py      # 探针：抓 Blender 原生进度输出
│   └── probe_driver.py      # 探针：验证驱动脚本 JSON 进度实时性
├── probes/
│   ├── probe_cycles.log     # Cycles 原生输出实测样本
│   └── probe_eevee.log      # EEVEE 无头渲染实测样本
├── AGENTS.md                # 给 AI / 未来的自己
├── DEVELOPMENT.md           # 架构 + 关键问题与方案
└── CHANGELOG.md
```

---

## 快速开始

主程序尚未编写。当前可复现的两件事：

**1. 重建 tkinter 环境**（本机托管 Python 不带 tkinter）

```bash
python tools/build_tkinter.py
```

脚本会自动下载官方安装包、逐层剥离 WiX 容器、提取 tcl/tk 组件，并在隔离目录里拼装出一套可用的 tkinter。

**2. 复现 Blender 输出格式实测**

```bash
python tools/probe_render.py
```

会在 `probes/` 下生成 Cycles 与 EEVEE 两条实测日志——进度解析的正则必须基于这两份样本，不要照抄网上 4.x 的方案。

---

## 致谢

文档结构遵循 [knowledge-base](https://github.com/Simiely/knowledge-base) 的《单项目规范》：四件套分层 + 生长式拆分。
