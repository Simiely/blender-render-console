# blender-render-console

<img src="assets/app_preview.png" width="96" alt="应用图标">

用代码（而不是 Blender GUI）启动渲染的 Windows 小工具：无头调用 Blender，实时显示**进度**与**预计结束时间**，崩溃后**自动续跑**。

---

## 为什么需要它

用 Blender GUI 渲染大型工程时容易整个进程崩掉，最典型的是 Windows 弹出「显示驱动已停止响应，并且已恢复」。

根因是 **Windows TDR（Timeout Detection and Recovery）**：GUI 模式下 GPU 既要驱动桌面、又要跑渲染，只要有一帧超过约 2 秒没能响应，Windows 就会强制重置显卡驱动，Blender 随之崩溃。

改用命令行无头渲染（`blender -b`）后，GPU 不再承担显示输出——等于把渲染从「桌面会话里的一条支线」变成「独占 GPU 的独立进程」。这是稳定性差异的主要来源，另外还顺带规避了 VRAM 被界面挤占、Persistent Data 跨帧累积、视口上下文抢 GPU、插件 hook 渲染流程等问题。

---

## 当前状态

> **v0.6.2：开机自启 + 任务存档 + 开工前磁盘空间预检** —— 点「开始渲染」会**先把任务存下来**，
> 之后无论崩溃、掉电还是手动关机，重新开机后程序自动读回这次任务并接着渲。
> 界面勾一下「开机自动启动」即可（写 `HKCU` 的 Run 项，免管理员权限）。
> 单文件 exe 仍可用：`dist/blender-render-console.exe`（双击即界面）/ `dist/brc.exe`（命令行）。

| 能力 | 状态 |
|---|---|
| **单文件 exe**（PyInstaller onefile，内含 tcl/tk） | ✅ 打包后自检 + 真窗口截图验证 |
| 图形界面（tkinter，深色主题） | ✅ 真窗口实测 |
| **选中工程自动读取渲染配置** | ✅ 真机实测：引擎 / 采样 / 设备 / 分辨率 / 帧范围 / 输出路径一次填好 |
| **多场景工程选场景渲染** | ✅ 真机实测：双场景（64x48 / 128x96）分别渲出，**以产物尺寸为证**；场景名写错有明确告警 |
| **开机自启 + 任务存档，开机自动接着渲** | ✅ 真机实测：强杀进程树 → 存档原封不动 → 重启从第 662 帧接着跑到 1550/4000 |
| **失败不自动续跑**（只有"崩溃"才续） | ✅ 真机 + 单测：失败会撤掉自动续跑并**把原因写进存档**，登录日志里直接说清 |
| 无头渲染 + 逐帧进度 | ✅ 真机实测（Cycles / EEVEE 各跑通） |
| ETA 自算（剔除首帧预热 + EMA 平滑） | ✅ 真机实测 |
| **崩溃 / 被杀后自动续跑** | ✅ 真机实测：中途 `taskkill` 掉 Blender，重启后只渲染剩余帧 |
| **一直重启，直到全部渲完**（带无进展护栏） | ✅ 真机 A/B 实测，见下 |
| 取消（停止按钮 / Ctrl+C）并保留进度 | ✅ 单测覆盖 |
| 命令行模式 | ✅ |
| **exe 图标与版本信息** | ✅ 图标逐像素比对；窗口图标核对；版本资源对齐 `__version__` |

### 崩溃/关机后自动接着渲，是怎么做到的

「点开始 → 先存档 → 再渲染」，存档在 `%LOCALAPPDATA%\blender-render-console\pending.json`，
记的是**完整任务定义**（`.blend`、场景、帧范围、引擎/采样/分辨率、输出模板、blender.exe 路径）。

关键是**没法在崩溃时写标记**（被强杀或掉电时进程根本来不及动），所以用反向设计：
开始时默认 `autoresume=True`，**只有进程活着报出结果时才撤销**（正常渲完删档，其余撤销并记下原因）。

| 发生了什么 | 存档 | 下次开机 |
|---|---|---|
| 崩了 / 掉电 / 被强杀（**没有代码执行的机会**） | 原封不动 | **自动接着渲** |
| 你主动点了「停止」/ 渲染中关窗 | `autoresume` 置 False + 记原因，断点保留 | 只回填表单并提示原因，**不自动跑** |
| 自己跑失败了（重试额度耗尽 / 抛异常） | 同上 | 同上 —— **一个注定失败的任务不该每次开机白跑一遍** |
| 全部渲完 | 删除 | 不会有动作 |
| 工程或 blender 挪了位置 | 保留（不删） | 提示原因，挪回去还能救 |

> 分界不是"成功还是失败"，而是「**进程有没有活着把结果报出来**」：被强杀/掉电时进程压根没机会
> 执行任何代码，标记自然还是 True；而取消、额度耗尽、异常都是活着的进程写出来的结果。

「一直重启」的实测口径 —— 用同一个「前 6 次启动必定失败」的启动器（`.bat`，之后原样转交真 Blender），
只改重启上限这一个变量：

| 重启上限 | 第 6 次崩溃后 | 产出 |
|---|---|---|
| `5`（默认） | 「重启次数已达上限」停止 | **0 帧** |
| `unlimited` | 继续重试 | **12/12 帧全部渲完** |

---

## 快速开始

### 直接跑 exe（推荐给只想用的人）

```bash
dist/blender-render-console.exe              # 双击也行：打开界面
dist/blender-render-console.exe --gui 工程.blend
dist/blender-render-console.exe --autostart  # 开机自启项用的就是这条（一般不用手敲）
dist/brc.exe 工程.blend -s 1 -e 240 -o out/frame_####   # 命令行版，保留终端输出
```

想开机自动接着渲：打开界面 → 勾上「**开机自动启动**」（旁边会显示登记到注册表的命令原文）。
它会往 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 写一项，**不需要管理员权限**，
取消勾选即删除。注意 exe 换目录后要重新勾一次（登记的是绝对路径）。

exe 已经把 tcl/tk 与驱动脚本一并打包（单文件、免安装、不依赖本机 Python）。
自己重新构建：

```bash
python tools/build_tkinter.py        # 只在第一次需要：生成 sidecar/（本机 Python 没有 tkinter）
python tools/build_exe.py --both     # 产出 dist/ 下两个 exe，并自动跑一遍打包后自检
```

### 图形界面（源码方式）

```bash
python main.py                       # 打开界面
python main.py --gui 工程.blend       # 打开界面并直接载入该工程（自动读配置）
python main.py --demo                 # 自检：自动填配置 + 用内置假 Blender 跑一轮（不碰真 Blender）
```

> 本机 Python **没有 tkinter**，界面靠 `sidecar/` 里的 tcl/tk 运行时。
> 缺失时先跑一次 `python tools/build_tkinter.py`（约 3 分钟，纯文件提取、不安装任何东西），
> 之后 `python main.py` 会自己带上 sidecar 启动。

界面流程：选 `.blend` → **自动把工程里的引擎 / 采样 / 分辨率 / 帧范围 / 输出路径填好**
（也可以点「读取工程配置」重读，或手改任意一项）→ 点「开始渲染」→ 看进度条 / ETA / 日志。
停止或崩溃后再点一次「开始渲染」，只渲染没完成的帧。

工程里**有多个场景**时：「读取工程配置」会把**每个场景**的参数一次读回来，
「场景」下拉变成可选（默认是工程里激活的那个），切换后上面的参数立即按该场景重填
—— 引擎 / 采样 / 分辨率 / 帧范围 / 输出路径都是**每个场景各有一套**的。
只有一个场景时下拉只作展示；某个场景没设相机的话会提前告警（Blender 会拒绝渲染它）。

长任务可以把「崩溃后重启」选成**「一直重启，直到全部渲完」**：选中后重启次数不限，
同时「单帧最多尝试」和「连续这么多轮一帧都没推进就停」会**自动填成 `0`（= 不限）**——
这才是选它的本意，否则剩下任意一个正数都会让任务在"其实还能接着重试"的时候提前结束
（帧被记成放弃踢出队列，或者撞上无进展上限直接刹车）。

那两个框**仍然可以改**：把「连续无进展轮数」填回一个正数（比如 `3`）就恢复了兜底 ——
如果在同一处反复崩、一帧都渲不出来，说明问题不在"重启次数不够"，程序会停下并明确提示，
而不是一直空转。这是内核里**唯一**防死循环的闸门，长期无人值守的渲染别把它关掉。

### 开工前的磁盘空间预检

点「开始渲染」之前会看一眼**输出目录所在盘**的可用空间，分两档处理：

| 档 | 条件 | 表现 |
|---|---|---|
| **拒绝开工** | 低于该盘的最低水位：**系统盘 10 GiB**、其它盘 2 GiB | 弹框说明，不开始，也**不留待办存档** |
| **提醒** | 低于"未压缩上界"估算的产出量（宽×高×每像素字节×帧数） | 日志里提醒一句，照常开始 |

系统盘的水位高得多，是因为**系统盘被写满会让 Windows 卡在开机或登录**（写不了 pagefile、
用户配置文件、Temp）—— 这是本程序唯一一条能搞到"打不开机器"的路径。
所以：**输出目录尽量别放 C 盘**，放别的盘并留够空间。

两条刻意的设计：

- **取不到空间信息就放行**（探测失败、路径不存在都算"不知道"）。拦不住的代价远小于误拦。
- 宽高留空（「保持工程设置」）时算不出产出量，**只有水位那一档生效** —— 宁可不说，
  也不编一个数字出来。要拿到估算，先「读取工程配置」把宽高填上。

### 命令行

```bash
# 渲染 1~240 帧，Cycles + OptiX，256 采样，输出到 out/frame_####.png
python main.py 工程.blend -s 1 -e 240 -E CYCLES --samples 256 --device OPTIX -o out/frame_####

# 只渲染若干指定帧
python main.py 工程.blend -f 1-10,15,20-25 -o out/frame_####

# 工程里有多个场景时：指定要渲哪个场景（不给 = 用工程里激活的那个）
python main.py 工程.blend -S 室内场景 -s 1 -e 240 -o out/frame_####

# 跑到一半 Ctrl+C 停掉，再执行同一条命令 → 从断点继续（不会重渲已完成的帧）
python main.py 工程.blend -s 1 -e 240 -o out/frame_####

# 想从头来：加 --no-resume
```

常用选项：

| 选项 | 说明 |
|---|---|
| `-s / -e / --step` | 帧范围与步长 |
| `-f` | 显式帧列表（`1-10,15,20-25`），**填了就只用列表** |
| `-S / --scene` | 渲染哪个场景（工程有多个场景时用）；不给 = 工程里激活的那个。名字写错会明确告警并回落到默认场景 |
| `-o` | 输出模板（**必须含 `####`**）或输出目录 |
| `-E` | 引擎：`CYCLES` / `BLENDER_EEVEE` / `BLENDER_WORKBENCH` |
| `--samples` | 采样数（Cycles 渲染采样 / EEVEE TAA） |
| `--device` | Cycles 设备：`CPU` / `CUDA` / `OPTIX` / `HIP` / `ONEAPI` |
| `--res / --pct / --format` | 分辨率 / 分辨率百分比 / 输出格式 |
| `--blender` | 手动指定 `blender.exe`（不给就自动扫描，扫不到会提示） |
| `--max-restarts` | 最多重启几次（默认 `5`）；填 **`unlimited`** / `-1` / `无限` / `一直` = 一直重启直到全部渲完 |
| `--max-no-progress` | 连续这么多轮一帧都没推进就停（默认 `3`，`0` = 不启用）；「不限次数」模式的唯一兜底 |
| `--max-frame-attempts` | 单帧最多尝试几次（默认 `3`，`0` = 不限）|
| `--no-resume` | 忽略已有断点文件 |
| `--verbose` | 连 Blender 原生输出一起打印 |
| `--log` | 把 Blender 原始输出落盘 |

终端输出长这样：

```
Blender：C:\Program Files\Blender Foundation\Blender 5.2\blender.exe
任务开始：D:\proj\scene.blend
  帧数 12 | 输出 D:\proj\out\smoke_#### | 断点 D:\proj\out\.render_state.json | 重启次数不限（连续 3 轮无进展才停） | 场景 室内场景
  实际设置：引擎 CYCLES | 320x240 @100% | 采样 24 | 输出 D:\proj\out\smoke_####
[1/12] 帧 1 完成 0.4s (预热帧，不计入基线) | 单帧 -- | 剩余 11 帧 | ETA --（尚无样本）
[2/12] 帧 2 完成 0.3s | 单帧 0.3s | 剩余 10 帧 | ETA 3.0s（均值（样本 1））
  ⚠ Blender 退出码 1（第 1 次重启），已完成 5/12，还剩 7 帧
  第 1 次重启，剩余帧 [6, 7, 8, 9, 10, 11, 12]
[12/12] 帧 12 完成 0.5s | 单帧 0.4s | 剩余 0 帧 | ETA 0.0s（EMA（样本 4））
任务结束：完成 12/12 帧 | 失败 0 | 放弃 0 | 重启 6 次 | 用时 18.0s
```

---

## 目录结构

```
.
├── main.py                  # 入口：无参数 → GUI，带参数 → CLI（PyInstaller 也从这里打包）
├── brconsole/
│   ├── gui.py               # tkinter 界面：控件 + 后台线程 + 事件队列
│   ├── guimodel.py          # 界面逻辑层（表单校验 / 事件→状态 / 日志缓冲，可单测）
│   ├── theme.py             # 深色主题（ttk 必须切 clam 才接受配色）
│   ├── inspect.py           # 读取 .blend 里已有的渲染配置（一次读回**所有场景**）
│   ├── tkboot.py            # 缺 tkinter 时带 sidecar 自举重启
│   ├── cli.py               # 命令行入口 + 终端事件渲染
│   ├── core.py              # 调度核心：子进程 / 双通道进度 / 崩溃续跑
│   ├── driver.py            # 运行在 Blender 进程内的驱动脚本（注入执行）
│   ├── parser.py            # Blender 原生输出解析（5.2 格式）
│   ├── eta.py               # ETA 估算（去预热 + EMA）
│   ├── state.py             # 断点状态文件（原子落盘）
│   ├── taskstore.py         # 待办任务存档（开机续跑的依据）
│   ├── autostart.py         # 开机自启（HKCU 的 Run 项）
│   ├── diskspace.py         # 开工前的磁盘空间预检（系统盘水位更高）
│   └── locate.py            # blender.exe 探测（文件系统扫描，不用注册表）
├── assets/
│   └── app.ico              # 应用图标（16~256 七层），由 tools/make_icon.py 生成
├── tests/                   # 265 条单测 + fake_blender.py（与真机同构的假进程）
├── tools/
│   ├── build_exe.py         # PyInstaller 打包（spec + Tree()），写图标与版本资源，默认跑一遍打包后自检
│   ├── make_icon.py         # 生成 app.ico（纯标准库自绘 + 手写 ICO 容器，不需要 Pillow）；--sheet 出自查图
│   ├── build_tkinter.py     # 从官方安装包提取 tcl/tk（本机 Python 无 tkinter）
│   ├── smoke_real_blender.py  # 真机冒烟 6 场景：渲染 → 杀进程 → 续跑 → EEVEE → 读配置 → 「一直重启」A/B → 多场景
│   ├── capture_screen.py    # 抓窗口/全屏 PNG（验证界面用，纯 ctypes）；--list 列窗口标题
│   ├── probe_render.py      # 探针：抓 Blender 原生进度输出
│   ├── probe_driver.py      # 探针：验证驱动脚本 JSON 进度实时性
│   ├── arch_metrics.py      # 架构度量：Ca/Ce/I、环、SDP 违规、扇入（改依赖后重跑它看数字）
│   └── check_boundaries.py  # 边界检查：把评审抓到的越界固化成 5 条可执行规则（有违规退出码 1）
├── probes/                  # Blender 5.2 实测输出样本（正则的依据）
├── _tkbuild/                # build_tkinter.py 的中间产物（下载的安装包 / 解包结果），可删
└── AGENTS.md / DEVELOPMENT.md / CHANGELOG.md
```

---

## 验证

```bash
# 单测（265 条，不依赖 Blender，约 50s）
python -m unittest discover -s tests -t tests -p "test_*.py"

# 架构度量：耦合（Ca/Ce/I）、依赖环、SDP 违规、扇入榜。**改依赖后重跑它**
python tools/arch_metrics.py

# 边界检查：5 条规则（GUI 边界 / 层方向 / 无环 / driver 隔离 / 裸 print）
python tools/check_boundaries.py       # 有违规则退出码 1

# 真机冒烟（需要 Blender 5.2，约 120s）
python tools/smoke_real_blender.py
#   1/6 完整渲染 6 帧
#   2/6 中途杀掉 Blender → 续跑
#   3/6 EEVEE 引擎切换 + 首帧预热
#   4/6 读取工程配置（无头 Blender 读 .blend）
#   5/6 「一直重启」A/B 对照：同一份"连崩 6 次"的启动器，5 次上限 vs 不限次数
#   6/6 多场景 -S 选场景（以产物尺寸为证）+ 场景名写错时的告警

# 打包（产出后自动跑 5 项自检：--help / exe 图标逐像素比对 / 版本资源 / 内置假 Blender / 完整 core 流水线）
python tools/build_exe.py --both

# 改图标后先扫一眼跨尺寸对照图（16px 才是任务栏里真正看到的那个）
python tools/make_icon.py --sheet sheet.png

# 界面自检：自动填一套配置并用内置假 Blender 跑一轮
python main.py --demo

# 开机自启模式（自启项用的就是这条）：认领未完成任务，有就自动续跑
python main.py --autostart
```

单测用的是 `tests/fake_blender.py`（输出与真机同构的假进程），验证**接线**；
真机冒烟验证**真机行为**（bpy API、路径替换、引擎别名、真被杀之后能不能接上、重启策略）；
界面与打包产物则用 `tools/capture_screen.py` 抓真实窗口来核对。

---

## 环境（本机实测）

| 项 | 值 |
|---|---|
| Blender | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`（5.2.2 LTS，内置 Python 3.13.13） |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER，16376 MiB，驱动 616.92 |
| 托管 Python | 3.13.12（`~/.workbuddy/binaries/python/versions/3.13.12`），**无 tkinter** → 靠 `sidecar/` |
| 7-Zip | `C:\Program Files\7-Zip\7z.exe`（提取 tcl/tk 用） |
| PyPI | 走腾讯云镜像可直连；Clash 代理反而超时 |

---

## 致谢

文档结构遵循 [knowledge-base](https://github.com/Simiely/knowledge-base) 的《单项目规范》：四件套分层 + 生长式拆分。
