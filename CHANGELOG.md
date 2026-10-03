# CHANGELOG

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

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
