# blender-render-console

用代码（而不是 Blender GUI）启动渲染的 Windows 小工具：无头调用 Blender，实时显示**进度**与**预计结束时间**，崩溃后**自动续跑**。

---

## 为什么需要它

用 Blender GUI 渲染大型工程时容易整个进程崩掉，最典型的是 Windows 弹出「显示驱动已停止响应，并且已恢复」。

根因是 **Windows TDR（Timeout Detection and Recovery）**：GUI 模式下 GPU 既要驱动桌面、又要跑渲染，只要有一帧超过约 2 秒没能响应，Windows 就会强制重置显卡驱动，Blender 随之崩溃。

改用命令行无头渲染（`blender -b`）后，GPU 不再承担显示输出——等于把渲染从「桌面会话里的一条支线」变成「独占 GPU 的独立进程」。这是稳定性差异的主要来源，另外还顺带规避了 VRAM 被界面挤占、Persistent Data 跨帧累积、视口上下文抢 GPU、插件 hook 渲染流程等问题。

---

## 当前状态

> **v0.2.0：命令行版本可用**（渲染 / 进度 / ETA / 崩溃续跑均已真机实测）。GUI 与 exe 打包是下一步，见 [DEVELOPMENT.md](./DEVELOPMENT.md) 第四节。

| 能力 | 状态 |
|---|---|
| 无头渲染 + 逐帧进度 | ✅ 真机实测（Cycles / EEVEE 各跑通） |
| ETA 自算（剔除首帧预热 + EMA 平滑） | ✅ 真机实测 |
| **崩溃 / 被杀后自动续跑** | ✅ 真机实测：渲染中途 `taskkill` 掉 Blender，重启后只渲染剩余帧 |
| 引擎 / 采样 / 分辨率 / 输出路径按命令行覆盖 | ✅ 不改动用户的 `.blend` |
| 取消（Ctrl+C）并保留进度 | ✅ 单测覆盖 |
| GUI 界面 | ⏳ 下一步 |
| PyInstaller 单文件 exe | ⏳ 下一步 |

---

## 快速开始

```bash
# 渲染 1~240 帧，Cycles + OptiX，256 采样，输出到 out/frame_####.png
python main.py 工程.blend -s 1 -e 240 -E CYCLES --samples 256 --device OPTIX -o out/frame_####

# 只渲染若干指定帧
python main.py 工程.blend -f 1-10,15,20-25 -o out/frame_####

# 跑到一半 Ctrl+C 停掉，再执行同一条命令 → 从断点继续（不会重渲已完成的帧）
python main.py 工程.blend -s 1 -e 240 -o out/frame_####

# 想从头来：加 --no-resume
```

常用选项：

| 选项 | 说明 |
|---|---|
| `-s / -e / --step` | 帧范围与步长 |
| `-f` | 显式帧列表（`1-10,15,20-25`） |
| `-o` | 输出模板（**必须含 `####`**）或输出目录 |
| `-E` | 引擎：`CYCLES` / `BLENDER_EEVEE` / `BLENDER_WORKBENCH` |
| `--samples` | 采样数（Cycles 渲染采样 / EEVEE TAA） |
| `--device` | Cycles 设备：`CPU` / `CUDA` / `OPTIX` / `HIP` / `ONEAPI` |
| `--res / --pct / --format` | 分辨率 / 分辨率百分比 / 输出格式 |
| `--blender` | 手动指定 `blender.exe`（不给就自动扫描，扫不到会提示） |
| `--max-restarts / --max-frame-attempts` | 最多重启几次 / 单帧最多尝试几次（默认 5 / 3） |
| `--no-resume` | 忽略已有断点文件 |
| `--verbose` | 连 Blender 原生输出一起打印 |
| `--log` | 把 Blender 原始输出落盘 |

输出长这样：

```
Blender：C:\Program Files\Blender Foundation\Blender 5.2\blender.exe
任务开始：D:\proj\scene.blend
  帧数 6 | 输出 D:\proj\out\smoke_#### | 断点 D:\proj\out\.render_state.json
  实际设置：引擎 CYCLES | 320x240 @100% | 采样 24 | 输出 D:\proj\out\smoke_####
[1/6] 帧 1 完成 0.4s (预热帧，不计入基线) | 单帧 -- | 剩余 5 帧 | ETA --（尚无样本）
[2/6] 帧 2 完成 0.3s | 单帧 0.3s | 剩余 4 帧 | ETA 1.3s（均值（样本 1））
  ⚠ Blender 退出码 1（第 1 次重启），已完成 5/6，还剩 1 帧
  第 1 次重启，剩余帧 [6]
[6/6] 帧 6 完成 0.5s (预热帧，不计入基线) | 单帧 0.4s | 剩余 0 帧 | ETA 0.0s（EMA（样本 4））
任务结束：完成 6/6 帧 | 失败 0 | 放弃 0 | 重启 1 次 | 用时 10.1s
```

---

## 目录结构

```
.
├── main.py                  # 入口（PyInstaller 也从这里打包）
├── brconsole/
│   ├── cli.py               # 命令行界面 + 终端事件渲染
│   ├── core.py              # 调度核心：子进程 / 双通道进度 / 崩溃续跑
│   ├── driver.py            # 运行在 Blender 进程内的驱动脚本（注入执行）
│   ├── parser.py            # Blender 原生输出解析（5.2 格式）
│   ├── eta.py               # ETA 估算（去预热 + EMA）
│   ├── state.py             # 断点状态文件（原子落盘）
│   └── locate.py            # blender.exe 探测（文件系统扫描，不用注册表）
├── tests/                   # 单元测试（42 条）+ fake_blender.py
├── tools/
│   ├── probe_render.py      # 探针：抓 Blender 原生进度输出
│   ├── probe_driver.py      # 探针：验证驱动脚本 JSON 进度实时性
│   ├── build_tkinter.py     # 从官方安装包提取 tcl/tk（GUI 阶段用）
│   └── smoke_real_blender.py  # 真机冒烟：渲染 → 杀进程 → 续跑
├── probes/                  # Blender 5.2 实测输出样本（正则的依据）
└── AGENTS.md / DEVELOPMENT.md / CHANGELOG.md
```

---

## 验证

```bash
# 单测（42 条，不依赖 Blender，约 30s）
python -m unittest discover -s tests -p "test_*.py"

# 真机冒烟（需要 Blender 5.2，约 30s）：渲染 → 中途杀 Blender → 续跑 → EEVEE 切换
python tools/smoke_real_blender.py
```

两者覆盖的东西不一样：单测用的是 `tests/fake_blender.py`（输出与真机同构的假进程），
验证**接线**（子进程管理、解析、续跑循环、取消）；真机冒烟验证**真机行为**
（bpy API、路径替换、引擎别名、真的被杀之后能不能接上）。

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

## 致谢

文档结构遵循 [knowledge-base](https://github.com/Simiely/knowledge-base) 的《单项目规范》：四件套分层 + 生长式拆分。
