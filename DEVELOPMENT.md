# DEVELOPMENT.md

> 架构说明 + 关键问题与方案。每次改动都在这里留下痕迹。
> 问题记录格式与 [knowledge-base](https://github.com/Simiely/knowledge-base) 完全一致，收尾时可零转换提炼进经验库。

---

## 一、项目概览

**目标**：做一个 Windows 上的单文件 GUI 工具（PyInstaller 打包 exe），用代码启动 Blender 工程渲染，实时看到进度与预计结束时间，崩溃后自动续跑。

**要解决的痛点**：Blender GUI 渲染大工程时整个进程会崩（多为 Windows 显示驱动超时重置），且崩了之后要从头再来。

**当前阶段**：**图形界面版本已可用**（v0.3.0）。GUI + 自动读工程配置 + 深色主题均已真窗口实测；只剩 exe 打包，见第四节。

---

## 二、架构说明

```
┌─────────────────────────────────────────────┐
│  gui.py（tkinter）/ cli.py                    │
│  只做展示与交互，业务逻辑在 guimodel.py        │
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

**依赖方向**：`gui`/`cli` → `core` → {`parser`, `eta`, `state`}；`driver` 独立（跑在 Blender 里，
和宿主代码只有 JSON 契约）。`core` 通过 `cmd_factory` 注入命令行拼装方式，
所以测试可以换成假进程而不动业务逻辑（`tests/fake_blender.py`）。

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

## 四、下一步（待办）

按优先级：

1. ~~**跑通 tkinter sidecar**~~ —— ✅ 2026-10-04 完成。`tools/build_tkinter.py` 全流程通过（下载 → 剥离 WiX 容器 → 定位 tcltk 组件 → 还原 430 个文件 → 自检）。
2. ~~**编写 `driver.py`**~~ —— ✅ 2026-10-04 完成（`brconsole/driver.py`）。逐帧渲染 + 每帧 flush `##PROG##{JSON}`，并覆盖引擎/采样/分辨率/输出路径（不改 `.blend`）。真机跑通 Cycles 与 EEVEE。
3. ~~**编写调度核心**~~ —— ✅ 2026-10-04 完成（`core.py` / `parser.py` / `eta.py` / `state.py`）。双通道读取、EMA ETA、状态文件原子落盘、崩溃续跑循环。
4. ~~**崩溃续跑实测**~~ —— ✅ 2026-10-04 完成（`tools/smoke_real_blender.py`）：渲染中途 `taskkill` 掉 Blender，退出码 1 → 第 1 次重启 → 只渲染剩余帧 `[6]` → 最终 6/6。
5. ~~**编写 GUI**~~ —— ✅ 2026-10-04 完成（`gui.py` / `guimodel.py` / `theme.py` / `inspect.py` / `tkboot.py`）。
   任务配置 + 进度条 + ETA + 日志面板 + 深色主题 + **选中工程自动读配置**，真窗口截图验证通过。
6. **PyInstaller 打包**：`--onefile --windowed`。已经处理好的部分：
   - driver 与 inspect 脚本都以**源码字符串内嵌**、运行时释放到临时目录（onefile 下包内文件不在磁盘上）
   - `tkboot` 会在缺少 tkinter 时找 `sidecar/`；打包时要把 sidecar 一起塞进 exe
     （建议用 `--add-data "sidecar;sidecar"`，并让 `tkboot` 在 `sys._MEIPASS` 下也找一遍 —— 这是下一步要改的点）
   - 界面图标、版本信息（`--version-file`）还没做

---

## 五、实测证据索引

| 文件 | 内容 |
|---|---|
| `probes/probe_cycles.log` | Cycles 4 帧动画完整原生输出（199 行，含同步阶段、Sample 行、Saved 行） |
| `probes/probe_eevee.log` | EEVEE 单帧无头渲染原生输出（13 行，含首帧 10.25s 预热证据） |
| `tools/probe_render.py` | 生成上面两份日志的探针 |
| `tools/probe_driver.py` | 验证驱动脚本 JSON 进度实时性（时间戳与真实耗时吻合） |
| `tools/build_tkinter.py` | tkinter 提取与拼装（GUI 阶段用） |
| `tools/smoke_real_blender.py` | **真机冒烟**：渲染 6 帧 → 杀掉 Blender → 续跑 → EEVEE 引擎切换 → 读工程配置 |
| `tools/capture_screen.py` | 抓窗口/全屏 PNG（ctypes 调 GDI + PrintWindow），用于核对界面布局与配色；`--probe x,y` 可采样像素颜色 |
| `tests/fake_blender.py` | 与真机同构的假 Blender（原生行 + JSON 进度 + 可指定帧崩溃），给续跑逻辑做端到端测试 |
| `tests/` | 89 条单测：ETA / 原生行解析 / 状态文件 / 续跑与取消 / 命令行拼装 / 界面逻辑 / 工程配置解析 |
