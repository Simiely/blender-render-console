# DEVELOPMENT.md

> 架构说明 + 关键问题与方案。每次改动都在这里留下痕迹。
> 问题记录格式与 [knowledge-base](https://github.com/Simiely/knowledge-base) 完全一致，收尾时可零转换提炼进经验库。

---

## 一、项目概览

**目标**：做一个 Windows 上的单文件 GUI 工具（PyInstaller 打包 exe），用代码启动 Blender 工程渲染，实时看到进度与预计结束时间，崩溃后自动续跑。

**要解决的痛点**：Blender GUI 渲染大工程时整个进程会崩（多为 Windows 显示驱动超时重置），且崩了之后要从头再来。

**当前阶段**：**已交付**（v0.6.5）。这一版是**第二轮全量代码审核**：把上一轮没逐行读完的
5 个大文件（core / gui / guimodel / inspect / taskstore）逐行读完，修掉两个真问题 ——
「一直重启」的存档回填后切回有限次数，两个限次框停在 0（=不限，等于把唯一的死循环闸门
静默关掉）；以及 `JobState.load` 对"合法 JSON + 错字段值"不设防，坏断点能把开机认领带崩
（无人值守路径，窗口版连 stderr 都没有）。同时按坑 #44 收拢了两处重复规则
（「同一任务」判据收进 `state.task_signature()`，断点路径收进 `core.state_file_for()`），
并给开机认领入口加了兜底日志。真机 7 项核对全过；单测 341 条全绿。

v0.7.0 的阶段说明：**过夜挂机三件套 + 版本切换**（对照竞品 BRQ / Render Manager
的标配功能）：①渲染期间**防睡眠**（微软官方 `SetThreadExecutionState` 机制，
任务结束/取消/崩溃都释放）；②**任务结束后关机/睡眠**（60 秒倒计时弹窗可取消，
只有「全部渲完」触发，取消/失败不动 —— 决策在 `guimodel.aftermath_plan` 纯函数）；
③**完成通知**（提示音 + 任务栏闪烁 + toast 尽力而为，三层互不拖累）；
④**Blender 多版本下拉**（locate 候选直接接界面）。
架构上：core 不拉电源支线 —— 防睡眠以 `power_guard=(keep, allow)` 由**组合根注入**
（与 cmd_factory 同款模式）；`power.py` / `notify.py` 都是 I=0 的叶子。
真机 14 项核对全过（含 toast 实弹）；单测 341 条全绿。

v0.6.4 的阶段说明：**全量代码审核**（`pyflakes` + `ruff` 高信号规则集
+ 编码 / 挂钟依赖 / shell 注入 / 资源泄漏四项专项扫描），修掉两个真问题：
假 Blender 的扩展名表与真 driver 已经分叉（只认 3 个格式，真实现有 13 个 —— 用假 Blender 跑的
所有测试都验不到 TIFF/BMP/WEBP 的输出文件名），以及模板自带 `.jpeg`/`.tiff` 时会被再补一次后缀
写出双扩展名。两者都补了会把它们锁死的用例（`tests/test_driver_contract.py`）。
另外更正了一条我自己写错的规则理由（`driver` 的 `bpy` 是惰性导入，app 侧 import 它并不会炸）。
真机冒烟 6/6 通过；单测 312 条全绿。

v0.6.6 的阶段说明：按"先复现取证 → 再检索外部依据 → 列计划"的顺序做了三项 ——
**界面单实例锁**（命名互斥量，微软推荐；锁文件+PID 是反模式：残锁挡人 / PID 复用 /
创建竞态。动机有 Blender 官方 #37974 佐证：两实例同渲互踩临时文件导致损坏）；
**输出补全规则统一**（`-o render` 原先 CLI 当文件名前缀、GUI 当目录，同一输入两种落点，
收成 `core.complete_output_template()`，CLI 行为变化见 CHANGELOG）；
**断点文件版本校验**（`version` 原先只写不查）。真机：双开第二进程被拒 +
已有窗口成功聚焦 + 强杀后无残锁。

v0.6.3 的阶段说明：在 v0.6.2（开工前磁盘空间预检）之上，按**量化架构评审**
的结论做了一轮结构优化：`parse_frames` 从 `cli.py` 挪进 `core.py`（消掉"界面依赖命令行"那条
方向错误的边 —— `cli` 的 Ca 2→1，只被组合根依赖了）、`"_brc"` 从 5 处硬编码收成单一来源
（放在新叶子模块 `brconsole/layout.py`，不放包根，见 CHANGELOG 0.6.3）、
`brconsole/__init__.py` 里过期的分层说明改正。补 `locate.py` 单测时又抓出两个真 bug
（显式指定的 `BRC_BLENDER` 被排序挤掉、`deadline=0` 被当成"无截止时间"），
并修掉一个靠挂钟的 flaky 用例。边界规则 5 → 7 条；单测 301 条全绿。
评审方法与判据：`tools/arch_metrics.py`（Ca/Ce/I、环、SDP）+ AGENTS 坑 #41/#42。

v0.6.2 的阶段说明：补上**开工前的磁盘空间预检**（`brconsole/diskspace.py`）：输出目录所在盘
低于最低水位（系统盘 10 GiB / 其它盘 2 GiB）就拒绝开工，低于"未压缩上界"估算则只提醒。
这是唯一一条能导致"**进不去桌面**"的路径 —— 系统盘被写满后 pagefile、用户配置文件、
Temp 全写不进去；Run 项本身是登录后才执行的，拦不住开机，安全模式也不加载它。
取不到空间信息一律放行；宽高留空时只有水位那档生效。真机 17 项核对全通过，
含真实弹框截图（`tmp_probe/gui_diskspace.py`）。

`dist/` 下有两个可直接分发的单文件 exe（图形界面版 / 命令行版），带图标与版本信息。
第四节待办清单已全部完成。

---

## 二、架构说明

```
┌─────────────────────────────────────────────┐
│  gui.py（tkinter）/ cli.py                    │
│  只做展示与交互，业务逻辑在 guimodel.py        │
│  帧范围是「段列表」（多段不连续 + 每段步长）：  │
│  段数据与展开在 guimodel，区间原语在 core      │
└──────────────────┬──────────────────────────┘
                   │ on_event(kind, payload)
┌──────────────────▼──────────────────────────┐
│  core.py · RenderJob                         │
│  子进程管理 · 双通道读取 · 续跑循环 · 取消      │
├──────────┬──────────────┬───────────────────┤
│ parser.py│    eta.py     │     state.py      │
│ 原生行解析│ 去预热+EMA ETA │  断点文件原子落盘  │
└──────────┴──────────────┴───────────────────┘

另有三条旁路（都不进渲染主循环）：
    inspect.py  选工程时另起一个无头 Blender 读 .blend 里的渲染配置 → 预填表单
    theme.py    深色主题（ttk 必须切 clam，否则配色配置一律无效）
    tkboot.py   本机 Python 无 tkinter → 带 sidecar 环境变量重启自己一次

还有两条与"跨进程存活"有关的旁路（渲染主循环完全不知道它们存在）：
    taskstore.py 任务存档（pending.json）：回答"这次任务**是什么**"，供开机续跑重建
    autostart.py 开机自启（HKCU 的 Run 项）：回答"下次开机谁来拉起它"
    ⇒ 分工：`state.py` 回答"**跑到哪了**"（每帧写），`taskstore.py` 回答"**要跑什么**"（点开始写）
                   │ subprocess（无黑窗，Windows）
┌──────────────────▼──────────────────────────┐
│  Blender 子进程  blender -b ... -P driver.py │
│  driver 源码由 core 释放到临时目录（onefile 兼容）│
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  driver.py（运行在 Blender 内）               │
│  覆盖引擎/采样/分辨率 → 逐帧 render           │
│  → flush「##PROG##」JSON 行                   │
└──────────────────┬──────────────────────────┘
                   │ 进度行经 stdout 实时回传
┌──────────────────▼──────────────────────────┐
│  产物：frame_0001.png · blender.log · state   │
└─────────────────────────────────────────────┘
```

**依赖方向**：`gui` → {`guimodel`} → `core` → {`parser`, `eta`, `state`}；`cli` 是**终端适配器**，只被组合根
`main` 依赖（界面不再借它 —— 帧范围解析 `parse_frames` 与 `parse_restart_limit` 一样放在 `core`）；
`driver` 独立（跑在 Blender 里，和宿主代码只有 JSON 契约，所以依赖图上**没有边**）。
依赖方向由 `tools/check_boundaries.py` 的 6 条规则守着，改完跑一次。
`core` 通过 `cmd_factory` 注入命令行拼装方式，
所以测试可以换成假进程而不动业务逻辑（`tests/fake_blender.py`）。

**帧号从哪来（三条入口，一个原语）**：
`core.expand_segment(start, end, step)` 是**唯一**的"一段区间 → 帧号"实现；
`core.parse_frames(spec, …)`（命令行 `-f 1-100x5,200-400x2` 的文本写法）与
`guimodel.segments_to_frames(segments)`（界面上的帧段列表）都调它 ——
所以"命令行会渲的帧"与"界面回显的帧"不会漂开（`test_matches_expand_segment` 钉着这条）。
反方向 `guimodel.frames_to_segments()` 把展开后的帧号还原成段（存档回填用），
往返有逐项相等的单测。

**GUI 的线程模型**（改界面代码前必读）：
`RenderJob.run()` 跑在后台线程，它的 `on_event` 只 `queue.put()`；主线程用 `root.after(80ms)`
定时取队列刷控件。tkinter 不是线程安全的，工作线程里碰控件迟早随机崩。
同理，「读取工程配置」也是一次后台线程 + 一个 `__inspect__` 事件回主线程。

### 三条设计主线

1. **双通道进度**：`driver.py` 在主循环里自己吐 `##PROG##{JSON}` 结构化行（拿到每帧精确耗时、成功/失败），同时解析 Blender 原生行兜底（用于显示"场景同步中"这类没有帧号的状态）。只靠原生行无法区分"正在编译着色器"和"卡死了"。
2. **ETA 自算**：不使用 Blender 的 `Remaining`。算法 = 剔除首帧预热 → 单帧耗时 EMA 平滑（α≈0.3）→ `剩余帧数 × 平滑耗时`。
3. **崩溃续跑**：外层守护子进程；异常退出后读状态文件得到已完成帧集合，重启 Blender 只渲染剩余帧。状态文件在每收到一帧完成事件时原子落盘。

---

## 三、关键问题与方案

### 问题：Blender 5.2 命令行进度输出格式与 4.x 完全不同

**TL;DR**：网上流传的 4.x 进度解析正则在 5.2 上匹配不到任何内容，必须按实测样本重写。

- **问题**：按老资料写的正则 `Fra:(\d+).*\| Time:([\d:.]+).*Remaining:([\d:.]+)` 跑完 4 帧动画，命中 0 行。
- **根因**：Blender 5.x 把渲染日志改成了带时间前缀与类别列的结构化输出。实测对照：

  | 4.x（网上资料） | 5.2.2 实测 |
  |---|---|
  | `Fra:1 Mem:1234.56M (Peak 1500.00M) \| Time:00:01.23 \| Remaining:04:32.10 \| Scene, View Layer \| Sample 64/128` | `00:03.906  render \| Fra: 4 \| Mem: 6M \| Sample 24/24` |

  5.2 的每行格式为 `<自进程启动起的秒表>  <类别>  | <内容>`，帧内进度为 `Sample X/Y`（Cycles）或 `Rendering X / Y samples`（EEVEE）。
- **解决**：正则改为匹配 `Fra:\s*(\d+)` / `Sample\s+(\d+)/(\d+)` / `Rendering\s+(\d+)\s*/\s*(\d+) samples` / `Remaining:\s*([\d:.]+)` / `Saved:\s*'(.+)'`，且不假设字段顺序。
- **预防**：进度解析属于"依赖上游输出格式"的代码，**必须先跑 `tools/probe_render.py` 拿真实样本再写正则**，样本留档在 `probes/`。Blender 大版本升级后第一件事是重跑探针。

---

### 问题：Blender 自带的 Remaining 是帧内剩余，不是总剩余

**TL;DR**：`Remaining` 挂在 sample 行上，只描述当前这一帧，拿它当总 ETA 会给出荒谬的数字。

- **问题**：想直接读 Blender 的 `Remaining` 省掉自己算 ETA。
- **根因**：5.2 把它放在采样进度行里（实测 `00:03.906  render | Fra: 4 | Remaining: 00:00.04 | Mem: 6M | Sample 1/24`），语义是"本帧剩余渲染时间"。4.x 时代它确实是跨帧的总剩余，语义已经变了。
- **解决**：总 ETA 一律自算：`(总帧数 − 已完成帧数) × 平滑单帧耗时`，并额外给出已完成帧的实测均值与中位数，便于用户判断估算可信度。
- **预防**：不要复用上游"看起来正好够用"的字段，先确认它的语义与统计口径。

---

### 问题：EEVEE 首帧 shader 编译预热让 ETA 虚高数十倍

**TL;DR**：首帧 10.25s、后续帧近乎瞬时，若不把首帧排除在基线外，ETA 会离谱。

- **问题**：用"已完成帧的平均耗时"估剩余时间，样本少时数字剧烈跳动且严重偏大。
- **根因**：首帧要编译着色器、加载渲染 kernel（实测 Cycles 日志里也有 `Loading render kernels (may take a few minutes the first time)`），这部分是**一次性成本**，不属于稳态单帧耗时。EEVEE 实测：整轮 12.61s，其中首帧 `Time: 00:10.25`。
- **解决**：`EtaEstimator` 把第 1 帧标记为 warmup，不计入 EMA 基线；总帧数很少（≤2）时才退回使用原始样本。EMA 用 α≈0.3 抑制帧间抖动。
- **预防**：任何"用历史样本外推"的估算，都要先问一句"第一批样本里有没有一次性成本"。

---

### 问题：Cycles 场景同步阶段没有任何进度信号，界面像卡死

**TL;DR**：大场景渲染前期会花数分钟在 `Updating Scene / Building BVH`，期间无帧号无采样进度。

- **问题**：点开始后长时间界面无变化，用户以为程序挂了。
- **根因**：Cycles 在真正采样前要先同步依赖图、构建 BVH。实测小场景这段是毫秒级，但大场景可长达数分钟，期间输出全是 `Updating ...` 系列行。
- **解决**：把原生输出里的同步阶段关键词（`Synchronizing object`、`Updating Scene BVH | Building`、`Updating Shaders`、`Loading render kernels`）映射成界面上的阶段文案，并记录"最后一条输出距今多久"作为心跳；超时无输出时提示"正在编译着色器 / 构建 BVH，这属于正常现象"。
- **预防**：设计进度展示时，要覆盖"没有百分比可显示"的长阶段，否则界面必然被误判为死机。

---

### 问题：Windows TDR 导致 GUI 渲染整进程崩溃

**TL;DR**：GUI 渲染时 GPU 同时驱动桌面和渲染，单帧响应超时会被 Windows 强制重置驱动，进程随之崩溃。

- **问题**：Blender GUI 渲染大工程时随机崩溃，典型报错「显示驱动已停止响应，并且已经恢复」。
- **根因**：Windows 的 TDR 机制为显示驱动设了约 2 秒的响应上限，超时即重置显卡驱动。GUI 渲染时 GPU 还要负责显示输出，长帧极易触发。此外 GUI 模式还叠加了：界面额外占 VRAM、Persistent Data 跨帧累积（已知的"第二帧崩溃"元凶）、视口上下文抢 GPU、插件启动即挂进渲染流程。
- **解决**：走 `blender -b` 无头渲染——不驱动显示器，从根上绕开 TDR；同时用独立进程隔离崩溃，配合断点续跑把"崩了从头再来"变成"崩了接着渲染"。
- **预防**：长耗时 GPU 任务在 Windows 上都应默认无头跑；必要时另行调高 TDR 延迟。

---

### 问题：本机所有 Python 都没有 tkinter

**TL;DR**：托管 Python 3.13.12、其 venv、Store 版、Blender 内置 Python 全部缺 tkinter，且环境不允许装新 Python。

- **问题**：原计划用 tkinter 做 GUI（零第三方依赖、打包体积小），但 `import tkinter` 直接 `ModuleNotFoundError`。
- **根因**：托管式 Python 发行版为控制体积不打包 tcl/tk 运行时；`find` 全盘确认无 `_tkinter.pyd`、无 tcl 库目录。Blender 内置 Python（3.13.13）同样是精简构建。另外本环境的 `install_binary` 工具不可用，无法安装带 tkinter 的发行版。
- **解决**：✅ **已打通**。从 python.org 官方安装包里**提取** tcl/tk 运行时组件，拼装成隔离的 sidecar 环境，见 `tools/build_tkinter.py`。自检输出：`tkinter OK, TkVersion = 8.6` / `Tk() 实例化 + 销毁 OK`。

  拼装过程中另外踩到三个坑（都已修）：
  1. `tcl86t.dll` 自身依赖 **`zlib1.dll`**。最初只收集 `_tkinter.pyd` / `tcl86t.dll` / `tk86t.dll` 三个文件，`import _tkinter` 直接报 `DLL load failed`。必须把 MSI 里根级 dll **整包收下**。
  2. `_tkinter.pyd` 文件名**自带下划线前缀**，不能用"文件名是否含下划线"来区分"根级二进制"和"路径编码名"，否则它会被误判跳过，报 `ModuleNotFoundError: No module named '_tkinter'`。
  3. Python 3.8+ 在 Windows 上**不再用 `PATH` 搜索**扩展模块的依赖 DLL，必须 `os.add_dll_directory()`。脚本会生成 `sidecar/Lib/sitecustomize.py` 自动注册，只要把 `sidecar/Lib` 放进 `PYTHONPATH` 即可生效。
- **预防**：选 GUI 方案前先跑一句 `python -c "import tkinter"` 确认底座可用，别等写完界面才发现；手工拼装 DLL 时，先用 `ctypes.WinDLL()` 逐个加载验证依赖闭合，比等到 `import` 报错再猜快得多。

---

### 问题：MSI 安装路线被沙箱彻底封死

**TL;DR**：python.org 安装包 `/quiet` 静默安装报 `0x80070643`，MSI 服务在本环境不可用。

- **问题**：想用官方安装包静默安装一个带 tkinter 的 Python 到隔离目录，一条命令解决。
- **根因**：burn bundle 日志显示 `Failed to configure per-user MSI package`，错误码 `0x80070643`（MSI 安装失败），重试 3 次后整体失败。本环境对 Windows Installer 服务有限制。注：`InstallAllUsers=0` + `TargetDir` + `PrependPath=0` 参数本身正确，是服务层被拦。
- **解决**：放弃"安装"，改为"提取"——用 7-Zip 逐层剥离 WiX burn bundle（PE → 附加流 → CAB 容器 → 各组件 MSI → 嵌套 cab1.cab → 实际文件）。这条路不碰系统服务，纯文件操作。
- **预防**：沙箱/受限环境里，优先选"解压即用"而非"安装"的分发方式。

---

### 问题：提取出的文件名无法唯一还原目录结构

**TL;DR**：CPython 的 MSI 把路径分隔符打成 `_`，但真实文件名里也含 `_`，纯字符串切分不唯一。

- **问题**：从 MSI 的 cab 里解出的文件是扁平名，如 `tcl_tcl8.6_encoding_cp1250.enc`，需要还原成 `tcl/tcl8.6/encoding/cp1250.enc`。
- **根因**：命名规则是"路径段用 `_` 连接"。但 `tcl_tcl8.6_tzdata_America_Argentina_Buenos_Aires` 里，`Buenos_Aires` 是**一个**文件名而非两级目录，无法靠切分确定。理论上需要解析 MSI 的 `!File` / `!Directory` 表。
- **解决**：只还原**有确定规则**的部分，其余明确放弃（见 `tools/build_tkinter.py`）：
  - `_tkinter.pyd` / `tcl86t.dll` / `tk86t.dll` —— 本身就是扁平名，直接可用
  - `Lib_tkinter_<X>` → `Lib/tkinter/<X>` —— tkinter 包是扁平的，规则唯一确定
  - `tcl_<root>_<子目录>_<文件>` → `tcl/<root>/<子目录>/<文件>` —— 子目录走已知白名单（`encoding`/`http`/`msgs`/`opt0.4`/`package`/`demos`/`images`/`ttk`）
  - **`tzdata/`（时区数据）、`nmake/`（构建辅助）直接跳过** —— 对 GUI 渲染工具无用途，且是唯一无法可靠还原的部分
- **预防**：面对"编码过的路径"，先确认能否拿到权威映射表（此处是 MSI 表）；拿不到就别做启发式猜测，把不可靠的部分显式裁掉。

---

### 问题：不能用注册表探测 Blender 安装位置

**TL;DR**：`reg.exe` 在本机被安全策略列入黑名单，探测只能扫文件系统。

- **问题**：想按常规做法读 `HKLM\SOFTWARE\BlenderFoundation` 定位 `blender.exe`。
- **根因**：本机安全策略禁止启动 `reg.exe`（明确提示"不可通过其他 shell 绕过"）。另外实测发现 `D:\BlenderPortable\5.2\` **只有 config/datafiles/scripts**，是用户配置目录而**不是程序本体**，全盘唯一的 `blender.exe` 在 `C:\Program Files\Blender Foundation\Blender 5.2\`。
- **解决**：探测逻辑改为文件系统扫描（常见安装路径 + 便携版目录模式匹配）+ 用户手动指定；不依赖注册表。已实现在 `brconsole/locate.py`。
- **预防**：不要默认注册表可用；把"自动探测失败后手填路径"作为一等公民路径保留。

---

### 问题：write_still 不替换 `####`，而 frame_path 会重复追加帧号

**TL;DR**：输出路径必须自己替换 `####` + 自己补扩展名，两边都不能交给 Blender。

- **问题**：第一版 driver 直接把 `scene.render.filepath = "out/f_####"` 交给 `bpy.ops.render.render(write_still=True)`，结果磁盘上出现一个**真叫 `f_####.png` 的文件**，6 帧互相覆盖只剩 1 张。
- **根因**：实测（同一场景三种写法对比）：
  1. `write_still=True` **不替换** `####`，按 filepath 原样落盘 → `f_####.png`
  2. 改成手动替换后（`f_0001`）以为可以用 `scene.render.frame_path(frame=f)` 算期望路径，结果它给出 `f_00010001.png` —— 因为 filepath 里已经没有 `#`，Blender 会在文件名后**再追加一次帧号**
  3. `use_file_extension=True` 只在路径无扩展名时补，与上面两条叠加后更难预测
- **解决**：`##PROG##` 里报的路径与真正写盘的路径都由 `driver.output_path_for(frame)` 统一算：手动替换 `####` → 模板自带扩展名就不再追加 → 补 `FORMAT_EXT` 映射的扩展名 → 渲染前 `use_file_extension = False`。渲染后再用 `os.path.exists` 复核，对不上就发 `warn`（实测这条 warn 救过一次）。
- **预防**：任何"交给上游格式化"的路径 API，先用一个 3 帧的小实验把**真实落盘名**打印出来再写代码；尤其是帧号占位符这种"看起来一定会替换"的东西。

---

### 问题：`-b` 下 Cycles 不在 engine enum 列表里，按列表过滤会把可用引擎判成不可用

**TL;DR**：Cycles 是 addon，启动初期 `RenderSettings.engine` 的 `enum_items` 只有 `['BLENDER_EEVEE']`，但**直接赋值 `'CYCLES'` 是成功的**。

- **问题**：为了兼容 4.2+ 的 `BLENDER_EEVEE_NEXT` 改名，写了个"先读 runtime enum、在列表里挑别名"的函数。结果真机日志疯狂警告 `引擎 'CYCLES' 设置失败（当前可选：BLENDER_EEVEE）`。
- **根因**：实测 `-b` 启动时 enum 只有 `['BLENDER_EEVEE']`；等到后续再读才变成 `['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES']`——**enum_items 是延迟刷新的**，拿它当准入白名单必然误杀。
- **解决**：改成"直接赋值 → 读回校验 → 失败再退别名 → 全失败才警告"，enum 列表只用于警告文案里给人看。顺带实测确认：5.2 只认 `BLENDER_EEVEE`（`BLENDER_EEVEE_NEXT` 会抛 TypeError），别名表按这个顺序退。
- **预防**：把"运行时枚举值"当能力探测要非常小心，addon 注册时机不同结论就不同；**探测的终点应该是"试着做一次并读回验证"，不是"查清单"**。

---

### 问题：尝试次数记错地方会让崩溃续跑直接失效

**TL;DR**：每轮开始时给所有待渲染帧 +1 尝试次数，会连"根本没轮到的帧"一起扣额度。

- **问题**：单测 `test_persistent_crash_exhausts_that_frame` 期望 `done=[1,2,4]`（第 3 帧一直崩、第 4 帧最后跑出来），实际只拿到 `[1,2]` —— 第 4 帧从未渲染过，却被判定"尝试次数耗尽"直接放弃了。
- **根因**：崩溃发生在第 3 帧，第 4 帧压根没开始。但"轮开始时批量 +1"让它平白扣掉 3 次额度，于是从续跑队列里消失。这正是崩溃续跑最典型的场景（崩在某帧、后面还有一堆没渲染），属于设计缺陷而不是小 bug。
- **解决**：尝试次数改为**在收到该帧 `frame_start` 事件时才 +1**，并立刻落盘（崩溃后额度必须还在，否则永远耗不尽、无限重启）。
- **预防**：给"重试额度"类的计数器计数时，先问一句"这个事件真的发生了吗"，不要用"本轮计划包含它"代替"实际执行过它"。

---

### 问题：ttk 主题不接受颜色配置，桌面深色界面白忙一场

**TL;DR**：Windows 上 ttk 默认用 `vista` 主题，`style.configure(background=...)` 一律被忽略；必须切到 `clam` 才可完全自定义。

- **问题**：给界面配了整套深色（面板 #252526、输入框 #3c3c3c），跑起来只有日志区是深色，输入框依然是白的。
- **根因**：ttk 在 Windows 上默认主题是 `vista`，它由系统绘制原生控件，**大部分颜色选项直接不生效**（不是报错，是静默忽略）。
- **解决**：`theme.apply()` 里先 `style.theme_use("clam")` 再逐类控件配色；下拉列表是 Tk 原生 Listbox，不走 ttk，得用 `root.option_add("*TCombobox*Listbox.background", ...)` 单独设。
- **预防**：改主题前先用 `style.lookup("TEntry", "fieldbackground")` 读回实际生效值，别靠眼睛猜；界面结果用 `tools/capture_screen.py` 抓真实窗口（可 `--probe x,y` 采样像素颜色）来确认，而不是读代码想象。

---

### 问题：读取工程配置要另起一个 Blender，且输出必须带标记

**TL;DR**：`blender -b 工程.blend -P 脚本` 才能拿到里面的配置，一次约 2~3 秒；从 stdout 里捞 JSON 必须靠独特标记。

- **问题**：想让界面"选中工程就自动填好参数"，但 .blend 是二进制，不启动 Blender 读不了。
- **根因**：Blender 的工程数据只能由 Blender 自己解析；同时 Blender 会往 stdout 打一堆自己的日志，裸 JSON 根本定位不准。
- **解决**：`inspect.py` —— 释放一段只读脚本（**绝不 save_mainfile**），输出一行 `##BRCINFO##{JSON}`，宿主按标记行反查。读取放后台线程（3 秒左右不能卡界面），失败也给明确错误文案（超时/路径错/工程损坏）。
- **预防**：跟 Blender 对话的地方一律"独特标记 + flush"（driver 的 `##PROG##` 同理）；任何"要起外部进程"的界面动作都得先设计好"慢"的反馈。

---

### 问题：Blender 的默认输出路径 `/tmp/` 不是用户设置的目录

**TL;DR**：`.blend` 里 `render.filepath` 默认就是 `/tmp/`（Windows 上也是这个内部路径），直接拿来当输出目录会填出一个不存在的路径。

- **问题**：读回来的 `output_path` 是 `/tmp\`，界面上就填了 `/tmp\frame_####`。
- **根因**：Blender 内部用 POSIX 风格路径，默认输出位置写死成 `/tmp/`；在 Windows 上这个目录不存在。
- **解决**：`output_template_from()` 判定 `/tmp`（含结尾分隔符）与"Windows 上 `/` 开头的路径"都视为**未设置**，退回工程所在目录 + `<工程名>_####`。
- **预防**：读上游配置时，要能区分"默认值"和"用户真的填过"——默认值直接透传往往得到无效路径。

---

### 问题：打包后的 exe 起不来 tkinter（`version conflict for package "Tcl"`）

**TL;DR**：exe 自带 Tcl 8.6.15，而系统里另一个软件把 `TCL_LIBRARY` 指向了 Tcl 8.6.12，沿用即冲突。

- **问题**：exe 双击后**无窗口、无报错**（`--windowed` 连 stderr 都没有），进程还活着 —— 看着像"界面没画出来"。
- **根因**：改用命令行版（`console=True`）跑同一段代码，才看到
  `Can't find a usable init.tcl ... version conflict for package "Tcl": have 8.6.15, need exactly 8.6.12`。
  本机 `TCL_LIBRARY` / `TK_LIBRARY` 被另一个软件（`D:\Tool\LoginStateSwitcher\_internal\_tcl_data`）设成了全局环境变量；
  `tkboot.apply_frozen_env()` 原先用 `os.environ.setdefault`（本着"别覆盖用户环境"的好意），结果正好沿用了那个错的目录。
  同时 tkinter 自己推导的路径（`<_MEIPASS>/lib/tcl8.6`）跟我们的布局（`_brc/tcl/tcl8.6`）也对不上。
- **解决**：`apply_frozen_env()` 改为**赋值覆盖** `TCL_LIBRARY` / `TK_LIBRARY`；并把 `_brc/DLLs` 也加进 `sys.path`
  （`_tkinter.pyd` 是顶层扩展模块，只进 PATH 找不到它）。
- **预防**：排查"GUI 起不来"**先切命令行版**拿 traceback —— `--windowed` 会把错误吃得干干净净。
  另外"尊重既有环境变量"在**自带运行时的场景下是反的**：我们比系统更清楚该用哪份 DLL。

---

### 问题：Tcl 只打包了 tcl8.6 / tk8.6，缺兄弟包目录

**TL;DR**：`_brc/tcl` 下必须有 `dde1.4` / `reg1.3` / `tcl8`，否则 Windows 上 Tk 初始化到一半失败。

- **问题**：按"只带用得到的两个目录"的思路打包，漏掉了 `dde1.4` / `reg1.3` / `tcl8`。
- **根因**：Tcl 的 `auto_path` 是 `[file dirname $tcl_library]`（即 `_brc/tcl`），
  而 Windows 上 Tk 要 `package require dde` / `registry`，它们的 `pkgIndex.tcl` 就在同级的 `dde1.4` / `reg1.3` 下。
- **解决**：spec 里改用 `Tree(sidecar/tcl, prefix="_brc/tcl")` 整棵打包，并把这些文件写进 `check_sidecar()` 的前置检查。
- **预防**：带"目录即环境"的运行时（Tcl / Python site / JAVA_HOME）不要挑子目录，宁可多带几 MB；
  这类缺文件的报错出现在**它内部初始化的中途**，根因离现象很远。

---

### 问题：「一直重启直到渲完」被单帧重试额度抢先耗尽

**TL;DR**：只放开重启次数没用 —— 崩够几次之后所有帧的单帧尝试额度先耗光，队列里已经没有可渲染的帧了。

- **问题**：单测里选了 unlimited，结果仍只拿到 `done=[1,2,4]`（第 3 帧崩几次后再也不试了）。
- **根因**：两个额度是**互相独立**的：`max_restarts` 管"还能重启几次"，`max_frame_attempts` 管"这一帧还能试几次"。
  放开前者而后者仍是默认 3，第 3 帧第 3 次崩掉后就永久出局。
- **解决**：`state.remaining()` / `exhausted()` 支持 `max_attempts <= 0` 表示**不限次数**；界面与命令行都允许填 0。
- **预防**：实现「不限次数」这类选项时，要顺着链路数一遍**还有哪些上限会先咬住**
  （重启次数、单帧额度、无进展护栏、超时），少一个这个选项就是假的。

---

### 问题：「一直重启」必须配一条死循环护栏

**TL;DR**：次数不限 + 崩溃是随机的 → 需要"连续多轮一帧都没推进就停"作为终止条件。

- **问题**：不限次数后，若崩溃与工程本身有关（一崩就崩在同一处、一帧都渲不出来），重试永远没有意义，但程序会一直重启下去。
- **解决**：新增 `max_no_progress_rounds`（默认 3，`0` = 不启用）：连续这么多轮 `len(pending_after) == len(pending)`
  就发 `no_progress` 事件并停止，文案明确说"重试解决不了的问题，检查工程或显存"。
- **预防**：凡是要做"无限重试"，必须同时想清楚"什么情况下必须放弃"，否则就是把死循环写进产品。

---

### 问题：用外部 taskkill 断言"崩溃次数"必然时灵时不灵

**TL;DR**：`taskkill` 的落点依赖渲染时序，实测崩溃次数在 5~7 之间浮动；得把"崩"变成确定事件才能断言次数。

- **问题**：真机冒烟想验证"连崩 7 次仍不放弃"，写法是"监测到新帧完成就 kill 掉 Blender"。
  同一份代码两次跑，一次崩 6 次、一次崩 4 次 —— 有一次 kill 打到了已经渲染完并退出的 PID。
- **根因**：外部击杀与 Blender 自然退出之间有竞态；渲染快慢每次不同（实测 28.6s vs 36.6s），落点无法保证。
- **解决**：改用 `.bat` 启动器 —— 前 N 次调用直接 `exit /b 1`（**确定地**"崩"），之后 `"%BLENDER%" %*` 原样转交真 Blender。
  于是崩溃次数确定、真渲染仍由真 Blender 完成，并做成 A/B 对照（同样连崩 6 次：`--max-restarts 5` 必须放弃且 0 帧产出，
  `unlimited` 必须撑过并渲完 12 帧）。
- **预防**：跨进程的时序型断言不要"跑一次看着对就算对"。要么把随机性消除（合成确定事件），要么换成能证伪的对照实验。
- **附**：`.bat` 可以直接作为 `subprocess` 的 `args[0]`（CreateProcess 会拉 cmd.exe），但**转交路径必须用反斜杠** ——
  写成 `C:/...` 会找不到而返回 1，届时"永远崩"和"该崩几次"就分不清了；批处理里读计数器别用 `set /p`
  （会把行尾 CR 一起吃进来，`set /a` 随即算错），要用 `for /f "usebackq delims="`。

---

### 问题：打包后 `--demo` 没有 `python.exe` 可以拉起来

**TL;DR**：自检必须走真实多进程路径，但 onefile exe 里没有独立解释器 —— 让 exe 自己再当一次子进程。

- **问题**：`--demo` 原本是 `[sys.executable, "tests/fake_blender.py", ...]`，
  打包后 `sys.executable` 就是 exe 本身，而且 `tests/` 并不在包里，自检直接失效。
- **解决**：把 `tests/fake_blender.py` 一起打进 `_brc/selftest/`，并加内部入口 `main.py --fake-blender`；
  冻结态下 `_demo_cmd_factory()` 返回 `[exe, "--fake-blender", ...]`。
- **预防**：任何"自己拉起自己"的测试脚手架，都要先问清楚**打包后它的 `argv[0]` 是什么**。

---

### 问题：exe 里读不到 `brconsole/driver.py`

**TL;DR**：PyInstaller 把 `.py` 模块收进 PYZ 归档，`_MEIPASS/brconsole/` 下**没有这个文件**；
而 driver 是「读源码字符串再注入给 Blender」的，必须有一份**真实文件**。

- **问题**：打包后的 exe 跑渲染时立刻退出、只返回 1，`dist` 里只留下一个空 state 文件，一帧都没渲。
- **根因**：`read_driver_source()` 直接 `io.open(DEFAULT_DRIVER)`，而
  `DEFAULT_DRIVER = <包目录>/brconsole/driver.py` 在 onefile 下指向 `_MEIPASS/brconsole/driver.py` ——
  PyInstaller 只把**数据文件**落到磁盘，模块在 PYZ 里，所以这个路径必然不存在。
- **解决**：打包脚本把 `brconsole/driver.py` 当**数据文件**另存一份到 `_brc/py/driver.py`；
  `read_driver_source()` 在源码路径不存在时去 `_MEIPASS/_brc/py/driver.py` 找，读不到就抛一条
  指向打包配置的明确错误。
- **预防**：任何「读自己的源码来注入/释放」的写法，在打包后都要重新确认一遍 ——
  **`.py` 源码不作为数据文件出现在 `_MEIPASS` 里**。本项目里 `inspect.py` 因为把脚本写成了字符串字面量
  （`SCRIPT = r'''...'''`）反而没这个问题，算是运气。

---

### 问题：失败时"一声不响"——同时踩中两个坑

**TL;DR**：控制台用 `✗`/`⚠`（不在 cp936 字符集里）→ `UnicodeEncodeError`，
而事件回调的异常被静默吞掉 → 该打的错误信息一个字都没打，进程只返回 1。

- **问题**：exe 渲染失败时，终端里"检测到多个 Blender…"之后就什么都没有了，连 traceback 都没有。
- **根因**（两个坑叠加）：
  1. Windows 控制台 exe 的 stdout 编码是 cp936，而 `✗`(U+2717) / `⚠`(U+26A0) **编不出来**，
     `sys.stdout.write()` 直接抛 `UnicodeEncodeError`；
  2. `core.emit()` 为了"回调出错不该把渲染搞挂"写了 `except Exception: pass` ——
     把那个编码异常也一并吞了，于是 `job_error` 事件永远显示不出来。
- **解决**：三处一起改 ——
  ① CLI 的符号换成 cp936 里有的（`×` / `※`，`✓` 没有用到）；
  ② `cli.main()` 开头对 stdout/stderr 做 `reconfigure(errors="replace")` 兜底；
  ③ `core.emit()` 把首次回调异常记进 `result["reporter_error"]`，`cli.main()` 在任务失败时
  **兜底再打一次** `失败原因：…`。
- **预防**：给程序加"符号美化"前先确认目标代码页能不能编码（`.encode('cp936')` 试一下就知道）；
  更重要的是——**静默吞异常的地方必须留痕**，否则它会把真正的错误一起埋掉。

---

### 问题：PyInstaller `datas` 条目顺序写反 —— 构建"成功"但文件不落地

**TL;DR**：`datas` 的元组是 **`(目标名, 源路径, 类型)`**。写反了不会报错，TOC 里也有记录，
但运行时 `_MEIPASS` 下什么都没有。

- **问题**：把 `tests/fake_blender.py` 加进包后，exe 里 `--fake-blender` 依然报"内置假 Blender 缺失"。
  看 `build/brc/brc/PKG-00.toc` 里那条**明明白白存在**。
- **根因**：写成了 `(源路径, 目标目录, 'DATA')`。于是"目标名"成了 `C:\...\fake_blender.py`、
  "源路径"成了不存在的 `_brc/selftest`；构建不校验、TOC 照记，但解包时那个文件根本不存在。
- **解决**：改成 `(目标名, 源路径, 'DATA')`，且**把"运行时能不能真的拿到"写进打包后自检**。
- **预防**：**"打包成功"不等于"产物可用"**。凡是有"塞进包里的资源"，都要在打包后真跑一次
  （本项目：`tools/build_exe.py` 默认执行 `verify()`，会核对 `--help`、**图标是否写进 PE**、
  **版本资源是否与 `__version__` 一致**、内置假 Blender，以及**完整 core 流水线跑一轮**）。

---

### 问题：图标直接等比缩放，16px 会糊成"一个圆"

**TL;DR**：小图标必须单独设计，不能把 256 那张缩下去。

- **问题**：256px 那张很好看（橙底 + 播放三角），但缩到 16px 后圆角被抗锯齿啃掉、
  整块变成近似圆形，三角只剩几个模糊的点 —— 而任务栏和资源管理器里看到的**正是**这个 16px。
- **根因**：圆角半径、三角大小都是按比例定的。尺寸越小，同样的"比例"占用的绝对像素越少，
  细节先于轮廓消失。
- **解决**：`make_icon.profile(size)` 按尺寸分档 —— ≤20px「铺满 + 小圆角(0.105) + 三角放大 1.30×」，
  ≤28px 中间档，≤40px 又缓一档，≥48 用设计稿原值。
- **预防**：`make_icon.py --sheet` 出一张**跨尺寸、跨明暗底**的放大对照图，
  改完图标先扫一眼再打包。只看 256 那张是会被骗的。

---

### 问题：ctypes 调 GDI 不设 argtypes，64 位下句柄溢出

**TL;DR**：`ctypes.windll` 的默认签名是 `c_int`，句柄会被截断。

- **问题**：从 exe 里读图标做自检时，`DeleteObject(info.hbmMask)` 抛
  `ctypes.ArgumentError: OverflowError: int too long to convert`。
- **根因**：`windll.gdi32` 的函数没声明 `argtypes`，`ctypes` 就按 `c_int`（32 位）传参，
  而 HBITMAP/HICON 是 64 位指针 → 装不下。
  报错指向的是"参数太长"，很容易误判成坐标/标志位传错了。
- **解决**：`_init_gdi()` 里一次性把用到的 User32/GDI32 函数签名全部声明
  （含 `PrivateExtractIconsW` / `GetIconInfo` / `GetObjectW` / `GetDIBits` / `DeleteObject`）。
- **预防**：**凡是用 ctypes 调 Win32，先设 `argtypes`/`restype` 再动手**。
  本项目 `tools/capture_screen.py` 也是同样的路子。
  另外提取图标要用 `PrivateExtractIconsW`（能指定尺寸），
  用 `ExtractIconExW` 拿到的是"系统大图标"尺寸（随 DPI 变），没法跟 ico 里的层逐像素比。

---

### 问题：`-S` 指定了不存在的场景，Blender 静默回落，退出码还是 0

**TL;DR**：选错场景会渲出一整套错图，而且**没有任何错误信号**。

- **问题**：2026-10-04 实测 Blender 5.2.2：

  ```
  blender -b multi.blend -S NoSuchScene --python-expr "import bpy;print(bpy.context.scene.name)"
  → ACTIVE= SceneB
  → Can't find scene: 'NoSuchScene' in file: '...multi.blend'
  → 退出码 0
  ```

  只有一行**英文**提示，退出码 0，然后照常用默认场景渲染。
- **根因**：`-S` 的失败不构成错误 —— Blender 只是"没找到就保持原样"。
- **解决**：driver 里自己做，并**回读校验**：
  - 首选 `bpy.context.window.scene = target`（实测 `-b` 下 `bpy.context.window` **不是** None，
    切完 `bpy.ops.render.render(write_still=True)` 渲的就是目标场景 —— 用产物尺寸交叉验证：
    `-S SceneA` → 64x48，`-S SceneB` → 128x96）
  - 兜底退 `bpy.context.window_manager.windows[0].scene`
  - 名字不存在 / 都切不过去 → `warn(...)` 中文明确告警，并把实际场景名随 `start` 事件带出去
  - 界面再加一道：按下「开始渲染」前比对读到的场景列表，不在里面就直接拦下
- **为什么不用命令行 `-S`**：那条路名字写错时正是上面这个静默行为，且要改 `cmd_factory`
  的签名（多一个参数）牵动测试与 `--demo` 路径；driver 内切换是**零侵入 + 可校验**。
  （`-S` 本身能用，官方文档写着 "Set the active scene for rendering"，实测也生效 —— 只是它不替你报错。）

---

### 问题：`//` 相对输出路径被当成"Windows 上不存在的绝对路径"丢掉

**TL;DR**：Blender 默认输出路径就是 `//`，而它被一条 Windows 兼容规则误杀。

- **问题**：读工程配置时，输出模板永远退化成 `工程名_####`，
  工程里明明写着 `//outA/frame_` 也读不出来 —— 多场景时两个场景的输出路径看起来一模一样，
  "按场景重填输出路径"这条就等于没生效。
- **根因**：`output_template_from()` 有一条"以 `/` 开头的路径在 Windows 上不存在 → 退回工程目录"
  （针对 Blender 的 `/tmp/` 默认值），而 `//` 是"**相对 .blend 所在目录**"，同样以 `/` 开头 → 被误杀。
- **解决**：先展开 `//xxx` → `<工程目录>/xxx`，再走后面的判断；`blend` 为空时无从展开，仍按未设置处理。
- **预防**：拿不准的路径写法先用真机工程跑一次 `read_blend_info()` 看输出，别无头写规则。

---

### 问题：界面下拉的"显示值"和"实际值"分成两张表，界面用错了那张

**TL;DR**：维护两份表，必然有一份变成死代码。

- **问题**：`guimodel.py` 里定义了 `RESTART_CHOICES`（中文文案 → 实际值）与
  `RESTART_VALUES`（裸实际值），界面写的是 `values=RESTART_VALUES` ——
  于是下拉框里直接显示 `1 / 3 / 5 / 10 / unlimited`，**英文 `unlimited` 就露在界面上**；
  而配套的中文文案那套从没被任何控件引用过（死代码）。
- **解决**：下拉**只放中文文案**，把"认文案"的责任交给解析层：
  `core.parse_restart_limit` 从"精确匹配几个英文单词"改成"包含无限关键词 + 抓开头数字"，
  于是 `"5 次（默认）"` → 5、`"一直重启，直到全部渲完"` → 不限。
- **预防**：**能用一个字符串同时承担显示与解析，就别搞映射层**。
  另外测试里加了一条"选项里不许出现 `unlimited`"的断言，防止再退回去。

---

### 问题：崩溃时进程来不及写文件，「崩溃后自动接着渲」必须反向设计

**TL;DR**：凡是"崩溃时记一笔"的设计都不成立 —— 改成"开始时默认要续跑，只有主动停止才撤销"。

- **问题**：需求是"系统崩溃后可读取上次记录继续渲染"。直觉设计是"崩溃时把任务标记成未完成"，
  但进程被 `taskkill /F` 强杀、或系统掉电时**根本没有执行任何代码的机会**，
  退出钩子、`atexit`、`finally` 全都不会跑。这类设计只覆盖"正常退出"，恰好漏掉真正要救的场景。
- **解决**：把标记**方向反过来**，落成 `taskstore` 里的 `autoresume`：
  - 点「开始渲染」→ **先存档**（完整任务 + `autoresume=True`），**再**起渲染线程
  - 正常渲完 → 删档
  - **进程活着报出结果**（用户停止 / 重试额度耗尽 / 抛异常）→ `autoresume` 置 False（**只改标记，不删档**）
  - 其它情况（崩了、掉电、被强杀）→ 没人动它，`autoresume` 保持 True
  开机判定四态：`run`（未完成且允许自动续跑）/ `prompt`（未完成但自动续跑已被关掉，只回填不自动跑）/
  `done`（已渲完，清档）/ `drop`（前置条件不满足，**保留存档**并提示原因）。
- **判据不是"成功还是失败"，而是"进程有没有活着把结果报出来"**：这一点是后来才想清楚的。
  最初把"明确失败"也留着 `autoresume=True`（理由写的是"关机导致的失败重试一次更划算"），
  但**关机时进程是被直接杀掉的、压根报不出失败**，所以那条理由站不住；反而制造了开机噪音 ——
  一个注定失败的任务（最典型：场景里没有相机）会**每次登录都白跑一遍**。现在：
  崩溃（没人动标记）→ 自动续跑；活着的进程报出的任何失败 → 撤标记 + **把原因记进存档**
  （`auto_reason`），这样登录后拉开窗口就能看到"不自动跑 —— 上次运行没能跑完（有帧一直失败）"。
- **实测**：造 4000 帧任务 → 跑起来 → 强杀整个进程树 → 存档完好（`autoresume=True`、断点 `created`
  时间戳停在首次启动）→ 重启从第 662 帧接着跑到 1550/4000；且把 `autoresume` 置 False 后重启，
  界面只提示"不自动跑 —— <原因>"，进度停在「就绪」。
- **预防**：设计"断电/强杀可恢复"的能力时，**先问"这一步在进程已经死掉的情况下还跑得到吗"**。
  跑不到，就得靠"事前默认值 + 事后撤销"来兜。反过来，凡是"活着的进程能报出来的结果"，
  都不该和"崩溃"共用同一套自动重试策略。

---

### 问题：存档只存 `JobConfig` 不够 —— `blender_exe` 不在里面

**TL;DR**：`blender_exe` 是 `RenderJob` 的构造参数、不是 `JobConfig` 的字段，漏了它开机重建不出任务。

- **问题**：`taskstore` 一开始只想存 `JobConfig.to_dict()`。但 `RenderJob(cfg, blender_exe)` 里
  **Blender 路径是单独传的**，`JobConfig` 根本没有这个字段；`resolution_percentage`、
  `max_frame_attempts`、`max_no_progress_rounds` 也不在断点文件（`state.py`）里。
  少了任何一个，开机重建出来的任务都会"缺胳膊少腿地按默认值跑"——表现是"渲是渲了，但不是我要的那个"。
- **解决**：存档 = `JobConfig.to_dict()` **加** `blender_exe`（`taskstore.save(cfg, blender_exe, ...)`）。
  另外把断点路径的规则（`state_path` 优先，否则输出目录下 `.render_state.json`）从 `RenderJob._init_state`
  里收上来到 `JobConfig.state_file()`，免得 `taskstore` 再写一遍同样的规则、两边跑偏。
- **预防**：加 `JobConfig` 参数时必须同步 `JOB_CONFIG_FIELDS`；`tests/test_core.py` 里有一条
  **对着实例属性核对清单**的断言（而不是对着 `to_dict()` 的输出核 —— 那是同义反复），加参数忘了补清单会直接红。

---

### 问题：界面给了开关，开关却不通电（`v_auto_resume` 是死控件）

**TL;DR**：勾选框建了、显示了，但**全项目没有一处读它** —— 勾上取消都一样，纯摆设。

- **问题**：「启动时自动续跑未完成任务」这个勾选，用户勾上也罢、取消也罢，行为完全一样：
  `_save_task()` 里写死了 `autoresume=True`，另一个勾选态变量 `v_auto_resume`
  除了 `Checkbutton(variable=...)` 那一处之外**没有任何引用**。
- **有多隐蔽**：它**长得完全正常** —— 能点、能勾、颜色变化都对，界面截图也看不出问题。
  这类 bug 单靠"看着界面对不对"永远发现不了，只能靠 ①对着控件变量全局搜引用；
  ②跑起来之后去**观察副作用**（存档里那个字段到底变没变）。
- **解决**：让它真的有作用：①点「开始渲染」时按勾选态写存档的 `autoresume`；
  ②勾选变化时**当场改已有存档**（取消勾选立刻生效，不用等下次点开始）；
  ③启动时用存档的真实值**回填勾选态**，而不是一直显示默认值。
  真机验证方式：直接把 `App` 建出来、`set()` 变量再触发 `command`，然后**读存档**核对字段。
- **预防**：凡是新增控件，落地时**同时**做两件事 —— 全局搜一遍变量名确认有消费方；
  以及问自己"我怎么**从外部观察到**这个控件产生了效果"。观察不到的开关，就是没接线。

---

### 问题：输出模板没有绝对化，开机自启会把产物和断点甩到别处

**TL;DR**：`blend` 一直绝对化了，输出模板却原样留着；而开机自启时进程的工作目录**和用户点开始时不同**。

- **问题**：`JobConfig.__init__` 里 `self.blend = os.path.abspath(blend)`，但
  `self.output_template = output_template` 原样留着。真正拿它写盘的是 Blender 子进程，
  而 `.render_state.json` 也放在"输出模板所在目录"里。
- **根因**：**工作目录会变**。Windows 的 Run 项（Microsoft KB 179365）只规定"数据值是一条命令行"，
  **没有任何指定工作目录的机制** —— 开机自启拉起来的进程，cwd 不是用户当初点「开始渲染」时的那个。
  于是相对模板会把产物写到别处，断点也落到别处；开机续跑找不到断点 → `progress_of()` 返回 None
  → 判定成"还没开始" → **把已渲的帧重渲一遍**（不报错、只是白跑，最难发现的那种）。
- **解决**：`normalise_output_template()` 统一绝对化，且**在"建 JobConfig 的那一刻"（= 用户点开始）**
  就定下来，而不是跑的时候现算。`//` 前缀要**先按工程目录展开再绝对化**
  （直接 `abspath("//out/f_####")` 会得到 `\\out\f_####` 这种 UNC 路径，实测如此），
  展开规则与 `inspect.output_template_from` **共用一份**，不写两遍。
- **实测**：相对路径 `-o relout/s_####` 真机跑通，产物与 `.render_state.json` 都落在
  "建配置时 cwd"下的 `relout/`；单测还带一条**把工作目录换掉再读回存档**的用例。
- **预防**：凡是会被**另一个进程、另一时刻**使用的路径，都在**配置诞生时**绝对化。
  另外注意"测试自己会不会掩盖 bug"：这条用例第一版把期望值也算在 `chdir` 之后，
  两边一起漂 → 照样通过；必须把期望值**在切换目录之前**取好，测试才有牙（已用"还原修复"的办法验证过它会红）。

---

### 问题：Run 项装不下超长命令行，却"看起来登记成功"

**TL;DR**：Microsoft KB 179365 规定 Run 项的数据值**不超过 260 个字符**；超了会写进去但开机不执行。

- **问题**：开机自启是把一条命令行写进 `HKCU\...\Run`。文档原文是
  "The data value for a key is a command line no longer than 260 characters."
  —— 超过限制时**注册表写入本身会成功**，指令界面显示"已登记"，可开机就是什么都不发生。
  "开关是开的、功能没生效"是最坏的一种状态：用户会以为是自己没设置对。
- **解决**：`autostart.MAX_COMMAND_CHARS = 260`，`enable()` 超限直接抛 `ValueError`（**不写**），
  `sync()` 把它转成给用户看的提示（"路径太长…请把程序挪到更短的目录"），界面则回滚勾选。
- **预防**：凡是"把配置写进别人管的存储"（注册表 / 服务配置 / 系统定时任务），
  先查一眼**它的硬限制**再动手；别让"写入成功"被当成"功能生效"。

---

### 问题：`--autostart` 每次登录都弹窗口，但没活干

**说明（当前行为，刻意保留）**：没有待办任务时，`--autostart` 会记一行"没有未完成任务，正常启动"
并把界面显示出来，**不会静默退出**。权衡：静默退出对用户是**不可观察**的 ——
一旦自启项哪天失效（被安全软件清掉、exe 挪了窝），用户看到的现象和"程序正常工作、只是没活干"
**完全一样**，无从排查。宁可每次多一个窗口，也不做会伪装成"正常"的静默。

### 已知限制：命令行渲染没有单实例互斥，可能与界面撞车

**界面侧已有锁**（v0.6.6 起，命名互斥量，`applock.acquire()` 在 `gui.run_gui` 里，
`--autostart` 也走这条路）；**但 `main.py 工程.blend -s 1 -e 240` 这条命令行渲染路径没有**
（写在这里免得下次当成 bug 查）：

- **现象**：一边用命令行渲一批帧、一边在界面上点「开始渲染」，且**输出模板与断点文件相同**时，
  两个进程会读同一份 `.render_state.json`、都认为"其余帧待渲"，于是**同一批帧渲两遍**
  （写的是同一批文件，后写的覆盖先写的）。
- **不会坏数据**：断点文件是**原子落盘**（临时文件 + `os.replace`），产物是整文件覆盖写，
  所以最坏结果是白烧 GPU，不会写出半张图或坏掉的断点。
- **为什么不给 CLI 也加锁**：命令行是脚本/无人值守场景，`--no-resume` 后重跑、并行渲不同
  工程（不同输出目录）都是正当用法，一把全局锁会把它们挡在门外。真要挡，应该按
  **输出模板**（而不是按进程）判定，那是另一个功能。

---

### 问题：多段不连续的序列帧，在界面上等于"没有"

**TL;DR**：内核早就支持多段，界面上却只有一个 90px 宽、没写语法、报错是 Python 原文的输入框。

- **问题**：`core.parse_frames` 从早期就支持 `1-10,15,20-25`，命令行 `-f` 也一直能用；
  但界面那一行是「起 / 止 / 步长 + 帧列表（填了就只用它）」——帧列表输入框 `width=20`，
  夹在两排控件最右边，视觉上几乎看不见，也没写它要什么格式。用户的原话是
  "现在的使用方式不明显"。等于**功能有，但没人用得上**。
- **根因**：那个框是"给命令行参数留的后门"，不是设计出来的界面。它既没有**输入指引**
  （占位/帮助文本），也没有**结果回馈**（填完看不到会渲哪些帧），更没有**错误定位**
  （`invalid literal for int() with base 10: '1~3'` 只说"某个字符串不是整数"）。
  三样缺一样都还能凑合，三样全缺就没法用了。
- **解决**：帧范围改成一个**段列表** —— 一段一行（起/止/步长）、可增可删、
  **每段自带步长**；下面一行实时回显「共 N 帧：…；合并后 …」；错误指出**第几段**。
  三个"必须能看见"的东西一次补齐：填之前看得懂、填完看得见结果、填错知道错在哪。
  命令行同步支持每段步长（`1-100x5`）。
- **预防**：见到"给 CLI 参数留的后门输入框"，就当它**不存在** —— 要么补上指引/回显/定位，
  要么改成一个真正的控件。判断标准：**用户能不能只靠界面说出"我现在会渲哪些帧"**。

---

### 问题：动态增删的控件行，位置拿"列表长度"当行号 → 删过之后就重叠

**TL;DR**：`grid(row=len(rows))` 在**删除**之后必然撞车，而且数据模型完全正常。

- **问题**：帧段列表的第一版，`_add_segment_row` 里写 `row.grid(row=len(self._seg_rows), ...)`。
  实测路径是"加三段 → 删中间那段 → 再加一段"：屏幕上只剩两行（段 1 和段 3），
  而 `_seg_rows` 里三段俱在、回显也算的是 26 帧。**数据对、显示错**，最难发现的一种。
- **根因**：`len(rows)` 只在"只增不减"时才等于"下一个可用网格行"。删掉一行后，
  列表长度比"已占用的最大行号"小，新建的行就落在**已被占据的格子**上（互相覆盖）。
  顺带还有个"空洞"：剩下的行仍停在原行号上（0 和 2），中间空一行。
- **解决**：把"位置"这件事收给 `_sync_segment_ui()` 一处 —— 它按 `enumerate` 重排
  `grid(row=i)`、重编段号、更新删除按钮的可用性。`_add_segment_row` 只负责建控件，不定位。
- **预防**：**凡是"行数会变"的容器，位置一律由一次性的重排函数决定**，别在创建时算。
  验证方式也别只断言数据模型：要断言 `winfo_ismapped()` 出来的**行数**和
  `grid_info()["row"]` 的**序列**（`tmp_probe/gui_segments.py` 里就是这么钉的，
  第一版正是这条抓出了 bug）。

---

### 问题：`wrap="none"` 的日志区把长行悄悄裁掉

**TL;DR**：只有竖向滚动条时，横着超出部分**永远看不到**，而那往往正是出错那一行。

- **问题**：日志 `tk.Text(wrap="none")` + 只有竖向滚动条。Windows 路径动辄上百字符
  （`C:/Users/.../渲染输出_0001.png`），一行的关键信息在右边缘之外，用户只看到半句。
- **根因**：`wrap="none"` 表示"不换行，超出部分不做处理"；配竖滚动条就等于**静默裁剪**。
- **解决**：`wrap="word"`。并且**实测过它够不够**（`tmp_probe/probe_wrap.py`：420px 宽下
  3 段测试文字，none=3 显示行＝没换行，word=5、char=5＝都换行）—— 也就是
  **`word` 对单个超长 token（路径）同样会在控件边界处断开**，不必退到 `char`（后者会把
  英文单词从中间劈开）。同族问题在 Label 上：`ttk.Label` 不换行，要设 `wraplength`，
  且得跟着容器宽度动态设（帧范围那行回显就是这么做的）。
- **预防**：**"只有竖向滚动条"是一个信号** —— 出现它就问一句"横向超出的内容去哪了"。

---

## 四、下一步（待办）

按优先级：

1. ~~**跑通 tkinter sidecar**~~ —— ✅ 2026-10-04 完成。`tools/build_tkinter.py` 全流程通过（下载 → 剥离 WiX 容器 → 定位 tcltk 组件 → 还原 430 个文件 → 自检）。
2. ~~**编写 `driver.py`**~~ —— ✅ 2026-10-04 完成（`brconsole/driver.py`）。逐帧渲染 + 每帧 flush `##PROG##{JSON}`，并覆盖引擎/采样/分辨率/输出路径（不改 `.blend`）。真机跑通 Cycles 与 EEVEE。
3. ~~**编写调度核心**~~ —— ✅ 2026-10-04 完成（`core.py` / `parser.py` / `eta.py` / `state.py`）。双通道读取、EMA ETA、状态文件原子落盘、崩溃续跑循环。
4. ~~**崩溃续跑实测**~~ —— ✅ 2026-10-04 完成（`tools/smoke_real_blender.py`）：渲染中途 `taskkill` 掉 Blender，退出码 1 → 第 1 次重启 → 只渲染剩余帧 `[6]` → 最终 6/6。
5. ~~**编写 GUI**~~ —— ✅ 2026-10-04 完成（`gui.py` / `guimodel.py` / `theme.py` / `inspect.py` / `tkboot.py`）。
   任务配置 + 进度条 + ETA + 日志面板 + 深色主题 + **选中工程自动读配置**，真窗口截图验证通过。
6. ~~**PyInstaller 打包**~~ —— ✅ 2026-10-04 完成（`tools/build_exe.py`），`dist/` 下产出两个单文件 exe：
   `blender-render-console.exe`（`--windowed`，双击即界面）与 `brc.exe`（保留控制台输出，排错用）。要点：
   - driver 与 inspect 脚本本就以**源码字符串内嵌**、运行时释放到临时目录（onefile 下包内文件不在磁盘上）
   - sidecar **整棵 `tcl` 目录**进包 → `_MEIPASS/_brc/{DLLs,Lib/tkinter,tcl}`，运行时由 `tkboot.apply_frozen_env()` 挂回
   - 打包用 **spec 文件 + `Tree()`**，不能用命令行 `--add-data`：sidecar 有几百个文件，
     命令行会超过 Windows 长度上限（`WinError 206 文件名或扩展名太长`）
   - `tests/fake_blender.py` 与 `brconsole/driver.py` 都要当**数据文件**带进包
     （前者给 `--demo` 自检，后者给"注入 driver 源码"用，见第三节对应问题）
   - 打包后**默认跑一遍自检**（`verify()`），含"完整 core 流水线跑一轮" —— 只看"打包成功"会漏掉
     资源没落地这类问题
7. ~~**exe 的图标与版本信息**~~ —— ✅ 2026-10-04 完成。`assets/app.ico` 由 `tools/make_icon.py` 生成
   （纯标准库自己画 + 手写 ICO 容器，**不需要 Pillow**），打包时经 `icon=` / `version=` 写进 PE 资源；
   窗口图标另走一份数据文件，运行时由 `gui.icon_path()` 定位。要点：
   - **小尺寸单独调过**：16px 只有 256 个像素，把 256 那张等比缩下去会糊成"一个圆"
     （圆角被抗锯齿啃没、三角只剩几个点）→ ≤40px 改用「铺满 + 小圆角 + 放大的三角」
     （`make_icon.profile()`）。改完图标务必 `--sheet` 扫一眼再打包
   - 版本号**唯一来源是 `brconsole/__init__.py` 的 `__version__`**，打包时现读、不另存一份
     （已经漂过一次：文档写到 0.3.0 而代码里还是 0.2.0）
   - 打包后自检新增两项：`check_icon()` 逐像素比对 exe 图标与 `assets/app.ico` 的 32px 那层；
     `check_version()` 读 PE 版本资源与 `__version__` 对齐
   - 读 PE 资源用纯 ctypes（`PrivateExtractIconsW` / `GetFileVersionInfoW`）。
     注意 **GDI 调用必须先设 `argtypes`**，否则 64 位下句柄按 `c_int` 传，
     报的是 `OverflowError: int too long to convert`（看着像参数类型写错，实为没声明签名）
8. ~~**多场景选择**~~ —— ✅ 2026-10-04 完成（v0.5.0）。工程里有多个场景时，界面「场景」下拉可直选
   （命令行对应 `-S / --scene`）；读一次配置就把**所有场景**的参数带回来，切换场景立刻按该场景重填表单
   （引擎 / 采样 / 分辨率 / 帧范围 / 输出路径都是 per-scene 的）。要点：
   - 场景切换必须**回读校验**：命令行 `-S` 名字写错时 Blender 静默回落、退出码仍为 0
     （见第三节对应问题），所以由 driver 自己切 + 校验 + 明确告警，界面另加一道前置拦截
   - 输出路径里的 `//`（相对工程目录，**默认写法**）要先展开再判断，否则会被"Windows 上不存在的
     绝对路径"那条规则丢掉（见第三节）
   - 断点文件新增 `scene` 字段：**换场景不再误续跑**（帧号一样但内容是另一张图）
   - 真机冒烟新增第 6 项：双场景工程（64x48 / 128x96）分别用 `-S` 渲染，
     **以产物尺寸作为"渲的是哪个场景"的硬证据**，另验场景名写错时是否告警
   - 顺带修掉界面「崩溃后重启」下拉显示英文 `unlimited` 的残留（见第三节"显示值与实际值两张表"）

---

## 五、实测证据索引

| 文件 | 内容 |
|---|---|
| `probes/probe_cycles.log` | Cycles 4 帧动画完整原生输出（199 行，含同步阶段、Sample 行、Saved 行） |
| `probes/probe_eevee.log` | EEVEE 单帧无头渲染原生输出（13 行，含首帧 10.25s 预热证据） |
| `tools/probe_render.py` | 生成上面两份日志的探针 |
| `tools/probe_driver.py` | 验证驱动脚本 JSON 进度实时性（时间戳与真实耗时吻合） |
| `tools/build_tkinter.py` | tkinter 提取与拼装（GUI 阶段用） |
| `tools/make_icon.py` | **生成应用图标**（纯标准库 + 4× 超采样，手写 ICO 容器，不需要 Pillow）；`--sheet` 出跨尺寸/跨明暗底对照图 |
| `assets/app.ico` | 图标本体：16/24/32/48/64/128/256 七层，≤128 用 BMP、256 用 PNG 编码 |
| `tools/build_exe.py` | PyInstaller 打包（spec + `Tree()`），写图标与版本资源，产出两个单文件 exe，并**默认跑一遍打包后自检** |
| `tools/smoke_real_blender.py` | **真机冒烟 6 场景**：渲染 6 帧 → 杀掉 Blender → 续跑 → EEVEE 引擎切换 → 读工程配置 → 「一直重启」A/B 对照 → 多场景 `-S` 选场景 |
| `tools/capture_screen.py` | 抓窗口/全屏 PNG（ctypes 调 GDI + PrintWindow），用于核对界面布局与配色；`--probe x,y` 采样像素；`--list` 列窗口标题 |
| `tools/arch_metrics.py` | **架构度量**：解析 import 建依赖图，输出 Ca/Ce/I 耦合表、依赖环（文件级）、SDP 违规、扇入榜；运行时 / `TYPE_CHECKING` / 函数体惰性三类依赖分开统计。**改依赖后重跑** |
| `tools/check_boundaries.py` | **边界检查**（5 条可执行规则）：GUI 边界 / 层方向 / 无环 / `driver.py` 隔离 / 裸 print。有违规则退出码 1；配套 `tests/test_boundaries.py` 是反向用例 |
| `tests/fake_blender.py` | 与真机同构的假 Blender（原生行 + JSON 进度 + 可指定帧崩溃 / 前 N 次启动必崩），给续跑逻辑做端到端测试 |
| `tests/` | 312 条单测：ETA / 原生行解析 / 状态文件 / 续跑与取消 / 重启策略 / 命令行拼装 / 界面逻辑 / 工程配置解析 / 场景与输出路径 / 输出模板绝对化 / 任务存档与续跑判定 / 开机自启开关（含 260 字符上限）/ 「一直重启」时限次的默认值（含"可改回"护栏）/ 磁盘空间预检（含"取不到就放行"与"系统盘水位更高"）/ 架构边界规则（反向用例：违规样本必命中、合规样本不误报）/ Blender 探测（深度限制 / 跳过噪音目录 / 环境变量优先级）/ driver 契约（扩展名表与假 Blender 逐项一致、落盘路径规则）/ 坏断点容错（合法 JSON + 错字段值 → 当没有）/ 单实例锁（二次 acquire 被拒 / 交还可再拿 / 崩溃后内核回收）/ 电源管理与善后决策（防睡眠成对 / 倒计时取消 / 只在渲完触发） |
