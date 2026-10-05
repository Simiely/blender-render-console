# CHANGELOG

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.6.3] - 2026-10-05

按量化架构评审的结论做的一轮结构优化（数字可用 `python tools/arch_metrics.py` 复现）。

### 修掉的结构问题

- **界面层不再依赖命令行适配器**。`gui.py` 原先为了借一个帧范围解析函数写了
  `from .cli import parse_frames` —— 一条"界面依赖命令行"的**方向错误**的边。
  按同性质的 `parse_restart_limit` 的做法，把 `parse_frames` 挪进 `core.py`
  （"用户输入字符串 → JobConfig 字段"的解析收在一处）。
  实测：**`gui` 那条 `→ cli` 的边消失**，`cli` 的 Ca **2 → 1**（现在只被组合根 `main`
  依赖，正是适配器该待的位置）。
  ⚠️ 更正：`gui` 的 Ce **仍是 9**（不是先前记的 8）—— 它换了一条边（`cli` → `layout`），
  数量没变。数字要按"边集合"读，不能只看计数。
- **`"_brc"` 从 5 处硬编码收成单一来源**（`tkboot` / `core` / `main` / `gui` /
  `tools/build_exe.py`），其中一处原来靠注释「与 tkboot.FROZEN_SUBDIR 保持一致」维持。
  这类"靠注释维持的一致性"断掉之后的现象是**打包后功能静默缺失** —— 不容易当场发现。
- **常量放进了新的叶子模块 `brconsole/layout.py`，不是 `__init__.py`**。
  第一版放在包根，看着能跑，但 `__init__` 立刻变成"谁都要来取一句"的汇聚点；
  这时谁按 PEP8 在 `__init__` 顶部补一句 `from .core import RenderJob`，
  `core → from .layout import ...` 就会撞上**还没执行到常量定义**的包，
  报 `ImportError: cannot import name 'FROZEN_SUBDIR' from partially initialized module`
  —— 已实测复现。放成叶子模块后这个雷从根上不存在（`layout` 的 Ca=4 / Ce=0 / I=0.00，
  正好在最稳定那一层）。配套第 7 条边界规则 `init-stays-leaf` 守着。

### 写单测时抓出来的两个真 bug（`locate.py`）

`locate.py` 是唯一"既没有单测、又不属于没法单测"的模块（没法单测的那类是
`driver.py`，它跑在 Blender 进程内）。补 `tests/test_locate.py` 的过程里抓到两处：

- **用户显式指定的 `BRC_BLENDER` 会被静默无视**：`find_blender` 末尾按 (版本, mtime)
  排序，自动探测到的更新一版会排到前面，而 `resolve_blender` 取的是 `cands[0]` ——
  于是"设置里明明写了路径"却跑了另一个版本。与模块自己那句"环境变量最优先：
  用户显式指定就是权威"直接矛盾。修法：显式指定的候选**钉在最前**，不参与排序。
- **`scan_root(deadline=0)` 被当成"没有截止时间"**：判断写成了 `if deadline:`，
  而 `0` 是假值。改成 `is not None`。（实际调用方永远传 `time.time() + timeout`，
  所以线上没暴露过，但 API 语义是错的。）

### 顺手修掉一个 flaky 测试

`test_core.py::test_cancel_mid_run` 用 `threading.Timer(1.3, job.cancel)` 赌
"这台机器 1.3 秒内能渲完第一帧"（时间线：起进程 ~0.4s → 第一帧完成 ~0.9s → 1.3s 取消，
余量只有 0.4s）。整机负载一高就赌输，`len(st.done) >= 1` 随机变红（实测 5 次红 1 次）。
改成**看到第一帧完成的事件再取消**，与机器快慢无关；取消仍从另一个线程发出，
与真实用法一致（界面上的「停止」按钮也在工作线程之外调）。

### 工程

- 边界检查 5 → **7** 条规则：新增 `adapters-for-root`（只有组合根可以依赖适配器，
  已验证能精准报出 `gui.py:25` 那条旧写法）与 `init-stays-leaf`（包的 `__init__.py`
  不许 import 子模块，已验证能报出包根循环导入的写法）。
- **修掉度量脚本自己的第 4 个解析盲点**：`__init__.py` 里的相对导入被解析成
  `core`（而不是 `brconsole/core`），解析不到本地节点就**整条边静默丢弃** ——
  正因为这个，新加的 `init-stays-leaf` 规则第一版对真实的违规样本一声不吭。
  根因是把"节点名"和"文件所属的包"当成了同一个东西（`brconsole/__init__.py` 的节点名是
  `brconsole`，而它自己就是那个包）。已拆成 `node_of()` / `package_of()` 两个函数。
- 单测 265 → **290** 全绿；`brconsole/__init__.py` 里过期的分层说明一并修正
  （原来漏了 6 个模块，还把 `cli.py` 写成"界面消费方" —— 正是这次修掉的那个方向）。
- 版本 0.6.3，重打包两个 exe。

## [0.6.2] - 2026-10-05

**开工前的磁盘空间预检** —— 堵死唯一一条能导致"进不去系统"的路径。

### 背景

输出目录是用户自己填的。系统盘被渲染写满之后，Windows 会因为写不了 pagefile、
用户配置文件、Temp 而卡在开机或登录 —— 这是本程序**唯一**能造成的"打不开机器"。
而在这之前，整个 `brconsole/` 里**一处空间检查都没有**。

### 变更

- 新增 `brconsole/diskspace.py`，判据分两档，都建立在确定性事实上（**不猜压缩率**）：
  - **拒绝开工**：可用空间低于该盘的**最低水位** —— 系统盘 `10 GiB`（Windows 自身要
    留 10~15% 或 ≥20 GB，低于 10 GB 会明显不稳），其它盘 `2 GiB`。
  - **提醒但不拦**：可用空间低于**未压缩上界**估算的产出总量
    （宽 × 高 × 每像素字节 × 帧数）。上界任何格式都超不过，所以这条只可能**偏早**
    提醒、不会漏。
- **取不到空间信息一律放行**：探测失败、路径不存在都当"不知道"。
  拦不住的代价远小于误拦。
- 接在两处：
  - `gui.on_start` —— 拒绝并弹框说明，**且不留待办存档**（不然下次开机它会被
    以 `drop` 态认领一次，白跑一趟提示）。
  - `taskstore.check_runnable` —— 开机续跑前的体检，空间不够记为 `drop`、**存档保留**。
    开机那一侧只认"拒绝"档：估算提醒不该让一台刚开机的机器什么都不做。
- 已知局限：宽高留空（「保持工程设置」）时算不出产出量，**只有水位那一档生效**。
  宁可不说，也不编一个数字出来。
- 单测 221 → 265；真机 17 项核对全通过（`tmp_probe/gui_diskspace.py`，含真实弹框截图）。

### 工程工具（架构评审产出，运行时代码未改动）

做了一轮量化架构评审（判据：Robert C. Martin 的 Ca/Ce/I、SDP、ADP），把结论固化成两个脚本，
免得同一个问题长回来：

- `tools/arch_metrics.py` —— 解析 import 建依赖图，输出耦合表（Ca/Ce/I）、
  依赖环（模块级 / 文件级）、SDP 违规、扇入榜；并把**运行时 / `TYPE_CHECKING` / 函数体惰性**
  三类依赖分开统计（Python 版的"剥离 import type"）。
- `tools/check_boundaries.py` —— 把评审抓到的越界固化成 5 条可执行规则。实测：
  全仓只有 `gui.py` / `theme.py` 碰 tkinter；依赖图 **0 环**、**0 条 SDP 违规**。
- 配套 `tests/test_boundaries.py` 是**反向用例**：每条规则造违规样本断言命中、
  造合规样本断言不误报（只跑正向的话，一个永远返回 `[]` 的脚本也照样"通过"）。

### 顺带修掉的坑

- `shutil.disk_usage` 在 Windows 上**要求传入已存在的目录**（不存在的路径会抛
  `FileNotFoundError`），而输出目录常常是用户刚填、还没建的 → 先向上降级到
  最近的已存在祖先。
- 注入式探测函数**不能再要求路径真实存在**，否则"别的盘""根本不存在的盘"这些规则
  压根没法测（总不能为了跑单测去插一根 U 盘）。
- `test_taskstore.TestCheckRunnable.test_ok` 原本会去读**真实**磁盘余量 ——
  那样换台机器就会红，改成注入固定字节数。

## [0.6.1] - 2026-10-05

**选「一直重启」时，另外两个限次自动填 `0`（不限）**。

### 变更

- 「崩溃后重启」选成**「一直重启，直到全部渲完」**时，「单帧最多尝试」和
  「连续这么多轮一帧都没推进就停」会**自动填成 `0`（= 不限）**。
  这才是选它的本意：只要这两个里还留着正数，任务就会在"其实还能接着重试"的时候提前结束
  —— 帧被判 `exhausted` 踢出队列，或者撞上 `no_progress` 直接刹车。
- **只改默认值，不锁控件**：想留兜底就把数字改回去。`max_no_progress_rounds` 是内核里
  **唯一**防死循环的闸门（卡在同一帧反复崩时，光靠"重启次数不限"永远不会停），
  所以没有把它做成不可改。
- 切回带次数的选项时**还原**你原来填的值（不会把 `0` 带进"5 次"这种有限次数的模式）；
  从**任务存档**回填时一律以存档里的值为准，不被默认值覆盖。
- 旁边那句说明改成按当前选项切换，不再是写死的一行。

### 说明

- **命令行不受影响**：`--max-restarts unlimited` 仍按你显式给的 `--max-no-progress` /
  `--max-frame-attempts` 走（两者的默认值写在 `--help` 里）。"自动填 0"是**界面**的默认值策略，
  内核侧 `JobConfig` 不强制 —— 否则 `--max-no-progress` 会被静默忽略，而
  `test_core.py::test_no_progress_guard_stops_unlimited_mode` 钉住的正是"不限次数 + 有兜底"这个组合。

## [0.6.0] - 2026-10-05

**开机自启 + 任务存档 + 启动自动续跑**：点「开始渲染」会**先把任务存下来**，
之后无论是崩溃、掉电还是你手动关机，重新开机后程序会自己读回这次任务并接着渲。

### 新增

- **任务存档**（`brconsole/taskstore.py`）：`%LOCALAPPDATA%\blender-render-console\pending.json`，
  存**完整 `JobConfig` + `blender_exe` + `autoresume` 标记**，原子落盘。
  只保留一个"当前任务"（渲染是独占 GPU 的重活，排多个任务既不能并发也让"续哪个"变得不确定）。
- **开机自启**（`brconsole/autostart.py`）：往 `HKCU\...\CurrentVersion\Run` 写一项，**免管理员权限**。
  界面「任务」区新增两个勾选：**开机自动启动** / **启动时自动续跑未完成任务**（后者默认勾）。
  勾选即时落到注册表，旁边显示**登记的命令原文**；若登记项指向别的位置（exe 挪过窝）会明确提示重登记。
- **启动时自动认领未完成任务**：命令行加 `--autostart`（自启项用的就是它）。判定四态：
  - 未完成且允许自动续跑 → 回填表单并**直接开跑**
  - 未完成但自动续跑已被关掉（你点过停止 / 上次以失败收尾 / 你取消了勾选）→
    只回填表单并提示，**不自动跑**；**关掉的原因会记进存档**，开机日志里直接说清
  - 已经渲完 → 静默清理存档
  - 前置条件不满足（工程/blender 挪了位置）→ 提示原因，**但存档保留**（挪回去还能救）
- `JobConfig.to_dict()/from_dict()`、`JobConfig.state_file()`（断点路径规则从 `RenderJob` 里收上来一处）。

### 设计要点

- **`autoresume` 是反向标记**：进程被强杀、系统掉电时**根本来不及写文件**，所以"崩溃时打个标记"
  这种正向设计做不到。改成**开始时默认"要自动续跑"**，只有**进程活着报出结果**时才撤销/删除。
  于是崩溃 → 存档原封不动 → 下次开机自动续跑。
- **崩溃要能续跑，失败要能停下来**：判据是「进程有没有活着把结果报出来」，不是「成功还是失败」。
  被取消、重试额度耗尽、抛异常都是**活着的进程**写出来的结果 → 撤掉自动续跑（断点保留）；
  而被强杀/掉电压根没有代码执行的机会 → 标记仍是 True → 自动续跑。
  若给"明确失败"也保留自动续跑，一个注定失败的任务就会**每次开机白跑一遍**。
- **用户主动停止 ≠ 删档**：只把 `autoresume` 置 False，断点文件原样留着 ——
  删了就成"没跑过"，用户还得从头来。

### 修复

- **「启动时自动续跑未完成任务」勾选是个死控件**：`v_auto_resume` 建了、也显示了，但**全项目
  没有一处读它** —— 勾上和取消都一样，纯摆设（"界面给了开关、开关不通电"）。现在它有两个作用点：
  ① 点「开始渲染」时按勾选态写存档；② 当场改已有存档的标记（取消勾选立刻生效，不用等下次开始）；
  启动时勾选态还会**对齐存档的真实情况**，而不是一直显示默认值。
- **输出模板没有绝对化 → 开机自启会把产物和断点甩到别处**：`blend` 一直是绝对化的，输出模板
  却原样留着，而真正拿它写盘的是 Blender 子进程、断点文件也照它的目录放。开机自启时工作目录
  和当初点「开始渲染」时**不是同一个**（Run 项只给一条命令行），相对模板会让两边都漂走，
  还会因为找不到断点把已渲的帧**重渲一遍**。现在 `JobConfig` 统一绝对化（`//` 前缀按工程目录展开）。
- **Run 项装不下的超长命令**：Microsoft KB 179365 规定 Run 项的数据值**不超过 260 个字符**，
  超了会"写进去了、开机却不执行"。现在超过就**拒绝登记**并提示把程序挪到更短的目录，
  而不是留一条"看起来开了、其实从来没生效"的自启项。
- **表单回填会静默丢掉 `extra_args` / `restart_delay`**：这两个是 CLI 专有参数、界面上没有控件，
  从存档回填 → 再 `to_config()` 时会变回默认值，也就是**重建出来的任务和上次跑的不是同一个**
  （"参数悄悄变了"比报错难查得多）。现在 `FormModel` 原样透传，并有往返单测盯着。

## [0.5.0] - 2026-10-04

**多场景工程**：一个 .blend 里有多个场景时，界面会提示并可**直接选**渲染哪个场景
（读一次配置就把所有场景的参数都带回来，切换场景立即按该场景重填表单）。
同时修掉界面上一处英文残留（「崩溃后重启」下拉显示的是裸值 `1/3/5/10/unlimited`）
和一个读取输出路径的老 bug（Blender 的 `//` 相对路径被丢掉）。

### 新增

- **多场景选择**（界面「场景」下拉 / 命令行 `-S, --scene`）：
  - `inspect.py` 一次读回**所有场景**的配置（`scene_details`），顶层字段仍是"激活场景"那份；
    比"切一次场景起一次 Blender"划算得多（读一次 ≈ 起一次 Blender）
  - 界面：工程有多个场景时明确提示「当前用哪个、一共有哪些」，下拉可切换，
    切换后**引擎/采样/分辨率/帧范围/输出路径**按该场景重填（这些都是 per-scene 的）
  - 只有一个场景时下拉只作展示；没有相机的场景会提前告警（Blender 会拒绝渲染它）
  - 渲染时由 driver 在 Blender 进程内切换场景并**回读校验**（见下方"修复"第一条）
  - 断点文件新增 `scene` 字段：**换场景不再误续跑**（帧号一样但内容是另一张图）
- `tools/smoke_real_blender.py` 新增第 6 个真机场景：双场景工程（64x48 / 128x96）
  分别用 `-S` 渲染，**以产物尺寸作为"渲的是哪个场景"的硬证据**；另验场景名写错时是否告警。

### 修复

- **`-S`/driver 切场景失败会静默渲染错场景**：Blender 命令行 `-S 不存在的场景` 时
  只往 stdout 打一行 `Can't find scene: 'xx'`，**退出码仍是 0**，然后拿默认场景照常渲染。
  现在 driver 自己激活场景 + 回读校验，名字对不上或切换失败都给出**中文明确告警**；
  界面还会在按下「开始渲染」前拦一道（读到的场景列表里没有这个名字就报错）。
- **`//` 输出路径被丢弃**：`//` 是 Blender 的"相对 .blend 所在目录"，**默认输出路径就长这样**。
  它被"以 `/` 开头 = Windows 上不存在"那条规则误杀，导致读配置时输出模板永远退化成
  `工程名_####`（多场景之间也就看不出区别）。现在先展开成绝对路径再判断。
- **界面上的英文残留**：「崩溃后重启」下拉框显示的是 `RESTART_VALUES`（裸的
  `1 / 3 / 5 / 10 / unlimited`）—— 于是英文 `unlimited` 直接露在界面上，而配套的中文文案
  （`RESTART_CHOICES`）从来没被任何控件用过。现在下拉只放中文文案，
  由 `core.parse_restart_limit` 认中文（"5 次（默认）"→5、"一直重启，直到全部渲完"→不限）。
- `采样 None` 这类把 Python 的 `None` 直接打到界面/终端的地方，统一显示为 `-`。

### 内部

- `core.parse_restart_limit`：改成"包含关键词 + 抓开头数字"，能解析界面文案（旧实现是精确匹配）。
- `JobConfig.scene` / `JobState.scene` / job.json 的 `scene` 字段贯通，
  `JobConfig.signature()` 与断点匹配判断都带上场景。
- 单测 101 → **126 条**。

## [0.4.0] - 2026-10-04

**单文件 exe 可用**：`dist/blender-render-console.exe`（双击即界面）与 `dist/brc.exe`（命令行，保留输出），
两者都带**应用图标**（exe 资源 + 窗口图标）与**文件属性里的版本信息**。
同时新增**「一直重启，直到全部渲完」**的重启策略。

### 新增

- **「一直重启，直到全部渲完」**：命令行 `--max-restarts unlimited`（也认 `-1` / `无限` / `一直`），
  界面下拉框对应「一直重启，直到全部渲完」。它由两个**互相独立**的旋钮共同构成：
  - `--max-no-progress N`（默认 3）：连续 N 轮一帧都没推进就停 —— 「不限次数」模式下唯一的死循环护栏
  - 单帧尝试额度允许 `0 = 不限`（`state.remaining` / `exhausted`）：否则单帧额度会先于重启次数耗尽，
    「一直重启」会形同虚设（崩几次就再也没有可渲染的帧了）
- `tools/build_exe.py` —— PyInstaller 打包（spec + `Tree()`），产出 GUI / 命令行两个单文件 exe
- **exe 图标与版本信息**：
  - `tools/make_icon.py` —— 纯标准库生成 `assets/app.ico`（16/24/32/48/64/128/256 七层，
    ≤128 用 BMP、256 用 PNG 编码），自己画 + 手写 ICO 容器，**不需要 Pillow**；
    `--sheet` 出跨尺寸 / 跨明暗底的放大对照图供自查
  - 打包时经 `icon=` / `version=` 写进 PE 资源；**窗口**图标另带一份数据文件，
    运行时由 `gui.icon_path()` 定位（不设的话任务栏和标题栏会顶着 Tk 自带的羽毛图标）
  - 版本号**唯一来源是 `brconsole/__init__.py` 的 `__version__`**，打包时现读、不另存一份
- **打包后自检扩到 5 项**：新增「exe 图标逐像素比对 `assets/app.ico` 的 32px 层」
  与「PE 版本资源 == `__version__`」，均用纯 ctypes 读（`PrivateExtractIconsW` / `GetFileVersionInfoW`）
- **打包后 `--demo` 自检仍可用**：把 `tests/fake_blender.py` 一起打进 exe，
  新增内部入口 `main.py --fake-blender` 让 exe 自己再当一次子进程 ——
  打包后没有独立的 `python.exe` 可以拉起来，而自检必须走**真实多进程**路径才测得出接线问题
- 真机冒烟新增 **场景五：「一直重启」A/B 对照** —— 用 `.bat` 启动器把「崩」变成**确定性事件**
  （外部 `taskkill` 的崩溃次数会浮动，见下），同一份「连崩 6 次」的启动器分别配两种上限对比结果
- `tools/capture_screen.py`：新增 `--list` 列窗口标题；`--window` 从「精确匹配」改为
  「精确优先，**退化为子串匹配**」（窗口标题常带动态后缀，精确匹配一改标题就抓不到）

### 本次实测确认的结论

- **exe 里的 tkinter 必须把整棵 `tcl` 目录一起打包**，只挑 `tcl8.6` / `tk8.6` 会缺
  `dde1.4` / `reg1.3` / `tcl8` —— Tcl 的 `auto_path` 是 `[file dirname $tcl_library]`，
  Windows 上 Tk 要 `package require dde` / `registry`，缺了会在 **Tk 初始化中途**报错，很难看出根因
- **别沿用系统里已有的 `TCL_LIBRARY`**：本机实测它指向另一个软件的目录
  （`D:\Tool\LoginStateSwitcher\_internal\_tcl_data`，Tcl **8.6.12**），而我们自带的是 **8.6.15**，
  沿用即 `version conflict for package "Tcl": have 8.6.15, need exactly 8.6.12`。
  修法是**赋值覆盖**（不是 `setdefault`），并且把 `DLLs` 也加进 `sys.path`（`_tkinter.pyd` 是顶层扩展模块）
- **exe 里读不到 `brconsole/driver.py`**：PyInstaller 把 `.py` 模块收进 PYZ 归档，
  `_MEIPASS/brconsole/` 下**没有这个文件**（只有数据文件才落到磁盘上）。
  而 `core.read_driver_source()` 是直接 `open()` 磁盘路径的 → `FileNotFoundError`。
  修法：把 `driver.py` 当**数据文件**另存一份到 `_brc/py/driver.py`，读取时优先找它
- **「静默失败」的元凶是编码**：`✗`(U+2717) / `⚠`(U+26A0) **不在 cp936 里**，
  而 Windows 控制台 exe 的 stdout 走的是 cp936 → `stream.write()` 抛 `UnicodeEncodeError`
  → 被事件回调的 `try/except: pass` 吞掉 → **一个字的错误信息都打不出来，进程只返回 1**。
  修法三处：符号换成 cp936 里有的（`×` / `※`）、`stdout.reconfigure(errors="replace")`、
  以及让 `core.emit` 把回调异常记进 `result["reporter_error"]` 并由 CLI 兜底再打一次
- **「一直重启」A/B 实测**（同一份「连崩 6 次」启动器，真 Blender 完成实际渲染）：
  `--max-restarts 5` → 第 6 次重启后「已达上限」停止，**0 帧产出**；
  `--max-restarts unlimited` → 撑过 6 次崩溃，**12 帧全部渲完**，无「已达上限」
- **`datas` 条目的顺序是 `(目标名, 源路径, 类型)`** —— 写反了构建照样"成功"、TOC 里也有记录，
  但运行时不会解包落地（实测 `_brc/` 下只有 `DLLs/Lib/tcl`，没有 `selftest`）。
  所以打包后自检必须**真的跑一遍**，不能只看"打包成功"
- **`.bat` 可以直接作为 `subprocess` 的 `args[0]`**（CreateProcess 会自己拉 cmd.exe 执行），
  退出码与参数都原样传递；但**转交路径必须是反斜杠** —— 写成 `C:/...` 会找不到而返回 1
- 用 `.bat` 读计数器时，`set /p` 会把行尾 CR 一起吃进来，导致 `set /a` 算错；
  改用 `for /f "usebackq delims="` 读文件才稳
- **小图标不能靠等比缩放**：256px 那张很好看，缩到 16px 后圆角被抗锯齿啃掉、
  整块糊成"一个圆"，三角只剩几个点 —— 而任务栏/资源管理器里看到的**正是** 16px。
  修法是按尺寸分档（`make_icon.profile()`）：≤20px 用「铺满 + 小圆角 0.105 + 三角放大 1.30×」，
  ≤28 / ≤40 各缓一档，≥48 才用设计稿原值
- **ctypes 调 GDI 必须先设 `argtypes`**：默认签名是 `c_int`，64 位下 HBITMAP/HICON 会被截断，
  报 `ctypes.ArgumentError: OverflowError: int too long to convert`（看着像"参数传错"，
  实为没声明签名）。另外从 exe 提图标要用 `PrivateExtractIconsW`（可指定尺寸），
  `ExtractIconExW` 给的是"系统大图标"尺寸（随 DPI 变），没法跟 ico 里的层逐像素比
- 读 PE 版本资源时，`StringTable` 的 key 必须与 `VarFileInfo` 的 `Translation` 对上
  （本项目 `080404b0` ↔ `[2052, 1200]`，即简体中文 + Unicode），否则 `VerQueryValue` 查 `FileVersion` 返回空

### 修掉的问题

- **exe 里 `read_driver_source()` 找不到 `brconsole/driver.py`**（在 PYZ 归档里，磁盘上没有）→
  改为数据文件 `_brc/py/driver.py` + 冻结态优先查找；读不到时给出明确报错
- **控制台 `✗`/`⚠` 在 cp936 下抛 `UnicodeEncodeError`，被回调静默吞掉 → 失败时一声不响** →
  符号改 `×`/`※`、`stdout.reconfigure(errors="replace")`、回调异常记入 `result["reporter_error"]`
  并由 CLI 兜底再打一次
- sidecar 打包只带了 `tcl8.6` / `tk8.6`，缺 Tcl 的兄弟包目录（`dde1.4` / `reg1.3` / `tcl8`）→ 改为整棵 `tcl` 目录
- `tkboot.apply_frozen_env()` 用 `os.environ.setdefault` 沿用系统的 `TCL_LIBRARY` → 改为赋值覆盖
- `datas` 条目把「目标名 / 源路径」写反（写成了 `(源, 目标, 'DATA')`）→ 条目进了 TOC 却不落地
- 打包后自检最初只单跑假 Blender，**绕过了 core** → 漏掉了 driver 注入这类问题；
  现在改成用 `.bat` 跳板让 `brc.exe` 走**完整 core 流水线**跑一轮（rc 必须为 0、产物必须齐）
- spec 里加内置数据文件时把路径写成了 `%s`（没引号）→ 打包报 `SyntaxError`，应为 `%r`
- 自检脚本自己按 UTF-8 解码 `--help` 输出 → 在 cp936 上抛 `UnicodeDecodeError`；改成按**字节**匹配
- 命令行在 `--max-no-progress 0` 时文案显示成「连续 0 轮无进展才停」→ 改为「不做无进展兜底」
- 界面上「重启次数」下拉的说明写的是内部取值 `unlimited`，与下拉显示文案不一致 → 改为显示文案
- 真机冒烟场景五最初用「外部 `taskkill` + 断言崩溃次数」→ 崩溃次数实测在 5~7 间浮动（击杀可能打到
  已退出的 PID），断言必然时灵时不灵 → 改为 `.bat` 确定性启动器
- `brconsole/__init__.py` 的 `__version__` 自 0.2.0 起就没跟着升（0.3.0 漏了）→ 本次对齐到 0.4.0
- 打包后自检的版本项**对相对路径会假失败**：`GetFileVersionInfoSizeW` 传相对路径返回 0，
  `_exe_file_version()` 于是静默返回 `None`，看着像"版本资源没写进去"。
  打包时没暴露是因为 `build()` 内部传的是绝对路径 → 现在读 PE 资源的函数入口一律 `os.path.abspath()`
- AGENTS.md 坑清单的编号 20/21 重复（新增条目时插错位置）→ 重排为 19~28 连续编号

### 待办

无。`DEVELOPMENT.md` 第四节 7 项全部完成，`dist/` 下两个 exe 可直接分发。

---

## [0.3.0] - 2026-10-04

**图形界面可用**：深色主题、选中工程自动读取渲染配置、进度条 + ETA + 日志面板、停止后可从断点续跑。
真窗口截图逐项核对过布局与配色（用 `tools/capture_screen.py` 抓图 + 采样像素）。

### 新增

- `brconsole/gui.py` —— tkinter 界面：任务表单、开始/停止、进度条、状态与 ETA、日志面板；
  渲染跑在后台线程，事件经 `queue` 回主线程刷新（tkinter 不能跨线程碰控件）
- `brconsole/guimodel.py` —— 界面**逻辑层**（不 import tkinter，因此可单测）：
  `FormModel` 表单校验与补全、`ProgressModel` 事件→状态字段、`LogModel` 待消费日志队列、`event_line` 事件文案
- `brconsole/inspect.py` —— **读取 .blend 里已有的渲染配置**（引擎 / 采样 / 设备 / 分辨率 /
  帧范围 / 输出格式与路径），选完工程自动预填表单；只读，不动用户的 `.blend`
- `brconsole/theme.py` —— 深色主题（VS Code 配色），含下拉列表等 Tk 原生控件的配色
- `brconsole/tkboot.py` —— 缺 tkinter 时自动带 sidecar 环境变量重启自己，用户只需 `python main.py`
- `main.py` 支持 `python main.py`（打开界面）/ `--gui 工程.blend`（载入工程）/ `--demo`（自检跑一轮模拟任务）
- `tools/capture_screen.py` —— 纯 ctypes(GDI) 抓窗口/全屏 PNG，支持 `--probe x,y` 采样像素颜色；
  沙箱里 PowerShell 的 `Add-Type` 被拦、装 Pillow 不值当，索性自己写
- `tests/test_gui_model.py`、`tests/test_inspect.py` —— 单测从 42 条加到 **89 条**

### 本次实测确认的结论

- **读取工程配置真实可行**：`blender -b 工程.blend -P 脚本` 约 3s 拿回全部字段（实测 smoke.blend：
  CYCLES / 采样 8 / CPU / 160x120 / 帧 1-6），输出用 `##BRCINFO##` 标记行回传
- **ttk 在 Windows 上默认主题不接受配色**：必须 `theme_use("clam")`，否则深色配置被静默忽略
- **Blender 的默认输出路径是 `/tmp/`**（Windows 上也一样），要当"用户没设置"处理，退回工程目录
- 深色界面下 `TEntry` / `TCombobox` / `TTLabelframe` / 进度条配色逐类显式设置才完整

### 修掉的问题

- `--gui` 启动时先 `import gui`（内含 `import tkinter`）再自举，导致缺 tkinter 时直接崩 —— 必须先自举再 import
- 帧解析语义统一为"**显式帧列表优先**"（填了 `-f`/帧列表就只用它，不再与 `-s/-e` 合并）
- 输出模板补全规则：没有扩展名的输入按"目录"处理，避免把 `D:/out` 填成 `D:/out_####`

### 待办

见 `DEVELOPMENT.md` 第四节第 6 项：PyInstaller `--onefile --windowed` 打包（含把 sidecar 塞进 exe、图标与版本信息）。

---

## [0.2.0] - 2026-10-04

**命令行版本可用**：无头渲染 + 实时进度 + 自算 ETA + 崩溃自动续跑，全部经真机实测。
GUI 界面与 exe 打包留到 0.3.0。

### 新增

- `brconsole/driver.py` —— 运行在 Blender 进程内的驱动脚本：按命令行覆盖引擎 / 采样 / 分辨率 / 输出格式 / 输出路径（**不改用户的 `.blend`**），逐帧渲染并 flush 一行 `##PROG##{JSON}` 进度事件
- `brconsole/core.py` —— 调度核心：`RenderJob` 管子进程（无黑窗）、双通道读进度、崩溃续跑循环、取消、静默检测
- `brconsole/parser.py` —— Blender 5.2 原生输出解析（`Fra:` / `Sample x/y` / `Rendering x / y samples` / `Saved:` / `Remaining:` / 同步阶段关键词）
- `brconsole/eta.py` —— ETA 估算：剔除首帧预热 + EMA（α≈0.3），样本 ≤2 时退回均值，并给出口径文案
- `brconsole/state.py` —— 断点状态文件，原子落盘（`tmp` + `os.replace`），记录已完成帧 / 失败帧 / 尝试次数
- `brconsole/locate.py` —— `blender.exe` 探测：环境变量 → PATH → 常见安装目录 → 便携版深度扫描，**不用注册表**
- `brconsole/cli.py` + `main.py` —— 命令行入口：帧范围 / 引擎 / 采样 / 设备 / 分辨率 / 输出模板 / 断点控制，终端实时进度
- `tests/` —— 42 条单测，含 `tests/fake_blender.py`（与真机同构的假进程，用于测续跑与取消）
- `tools/smoke_real_blender.py` —— 真机冒烟：渲染 → 中途杀掉 Blender → 续跑 → EEVEE 引擎切换

### 本次实测确认的结论

- **崩溃续跑真的能接上**：Cycles 渲染 6 帧中途 `taskkill` 掉 Blender（退出码 1）→ 自动第 1 次重启 → 只渲染剩余帧 `[6]` → 最终 6/6。断点来自每帧原子落盘的 state
- **输出路径两边都不能交给 Blender**：`write_still` 不替换 `####`（会写出 `f_####.png` 并互相覆盖）；`frame_path()` 在 filepath 无 `#` 时会再追加一次帧号（`f_00010001.png`）。改为手动替换 + 手动补扩展名 + `use_file_extension=False`
- **Cycles 不在启动初期的 engine enum 列表里**（addon 延迟刷新），但直接赋值 `'CYCLES'` 成功 —— 按 enum 清单做准入会误判。5.2 只认 `BLENDER_EEVEE`，`BLENDER_EEVEE_NEXT` 抛 TypeError
- **EEVEE 首帧 shader 预热真实存在**：实测首帧 2.6s、后续帧快得多，预热标记按「每个子进程的第一帧」重新计算（重启后也要再剔一次）
- 引擎切换（CYCLES ↔ BLENDER_EEVEE）与采样覆盖在真机上均生效

### 修掉的问题

- 尝试次数原本在「每轮开始时」给所有待渲染帧 +1，导致崩在第 3 帧时第 4 帧（从未渲染）也被扣光额度被放弃 —— 改为只在收到该帧 `frame_start` 时 +1 并立刻落盘
- `fmt_duration` 小时格式串少一个参数（`TypeError: not enough arguments`）
- `Time: xx (Saving: xx)` 这类帧末统计行会被误识别成「正在写文件」阶段，已在阶段识别里排除

### 待办

见 `DEVELOPMENT.md` 第四节：GUI 界面（tkinter sidecar 已就绪）→ PyInstaller `--onefile --windowed` 打包。

---

## [0.1.0] - 2026-10-04

调研与可行性验证阶段。**主程序尚未编写**，本版本只包含实测证据、工具脚本与文档。

### 新增

- `tools/probe_render.py` —— Blender 命令行渲染输出探针，抓取 Cycles / EEVEE 的真实原生输出
- `tools/probe_driver.py` —— 验证「驱动脚本 + `sys.stdout.flush()`」能否实时回传 JSON 进度行
- `tools/build_tkinter.py` —— 从 python.org 官方安装包逐层剥离提取 tcl/tk 运行时，拼装隔离的 tkinter sidecar
- 四件套文档（README / AGENTS / DEVELOPMENT / CHANGELOG）

### 已验证的结论

- Blender **5.2.2 LTS** 的渲染进度输出格式与 4.x 时代**完全不同**，网上流传的解析正则全部失效
- Blender 自带的 `Remaining` 字段在 5.2 中语义为**帧内剩余**，不能作为总 ETA 使用
- EEVEE 可以在 `-b` 无头模式下正常渲染（实测通过，无 GPU 报错）
- 首帧包含 shader / kernel 编译预热，必须从 ETA 基线中剔除（EEVEE 实测首帧 10.25s，后续帧近乎瞬时）
- Cycles 在 `Updating Scene / Building BVH` 阶段不产生任何进度信号，需单独识别并给出界面提示
- 驱动脚本逐帧回传结构化进度可行，时间戳与真实渲染耗时严格对应
- **tkinter sidecar 拼装成功**：从官方安装包提取 tcl/tk 运行时并还原目录结构（还原 430 个文件），自检输出 `tkinter OK, TkVersion = 8.6` / `Tk() 实例化 + 销毁 OK`。本机 Python 原本完全不带 tkinter，且不允许安装，靠纯文件提取解决

### 环境探测结论

- 本机托管 Python 3.13.12 **不含 tkinter**；venv、Store 版、Blender 内置 Python 3.13.13 同样不含
- 本环境 `install_binary` 工具不可用，且 **MSI 服务被沙箱限制**（`0x80070643`），无法静默安装带 tkinter 的 Python
- `reg.exe` 被安全策略列入黑名单，Blender 位置探测只能走文件系统扫描
- 全盘唯一的 `blender.exe`：`C:\Program Files\Blender Foundation\Blender 5.2\`
  （`D:\BlenderPortable\5.2\` 实测只是配置目录，不是程序本体）
- Clash 代理对 PyPI 反而不通，直连腾讯云镜像正常

### 待办

见 `DEVELOPMENT.md` 第四节：tkinter sidecar 验证 → driver.py → 调度核心 → GUI → 打包 → 崩溃续跑实测。
