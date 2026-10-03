# CHANGELOG

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

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
