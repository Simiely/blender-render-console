# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-04（commit `ffd358d`）完成四件套初始化
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

---

## 技术栈（精确版本）

- **Blender 5.2.2 LTS**，内置 Python 3.13.13，引擎 Cycles / EEVEE，渲染设备 OptiX 或 CUDA
- **宿主 Python 3.13.12**（托管版，**不含 tkinter**）——GUI 依赖需从官方安装包提取后拼装
- 目标产物：PyInstaller 打包的 **单文件 GUI exe**（`--onefile --windowed`）
- 平台：Windows（bash 走 Git Bash）

## 关键坑（越具体越好，改代码前必读）

1. **Blender 5.2 的进度输出格式与 4.x 完全不同**。5.2 是 `00:03.906  render | Fra: 4 | Mem: 6M | Sample 24/24` 这种带时间前缀的行；网上 4.x 的 `Fra:1 Mem:... | Time:... | Remaining:... | Scene, View Layer` 正则**匹配不到任何东西**。任何解析改动都必须先跑 `tools/probe_render.py` 对照 `probes/` 里的真实样本。
2. **Blender 自带的 `Remaining` 不能用**。5.2 里它挂在 sample 行上，表示**当前帧内**的剩余时间（实测 `Fra: 4 | Remaining: 00:00.04 | Sample 1/24`），不是整个队列的总剩余。总 ETA 必须自己算。
3. **首帧必须从 ETA 基线里剔除**。首帧包含 shader / kernel 编译预热——实测 EEVEE 首帧 10.25s 而后续帧近乎瞬时，不剔除会让 ETA 虚高几十倍。
4. **驱动脚本里每个输出行都要 `sys.stdout.flush()`**。`-b` 模式下 stdout 不是行缓冲，不 flush 会被块缓冲吞住，界面看起来永远卡在第一帧。
5. **Blender 要报错就让它报错**：加 `--python-exit-code 1`，否则 Blender 会吞掉脚本异常并返回 0，外层误判渲染成功。
6. **`reg.exe` 在本机安全策略的黑名单里**，不能用注册表探测 Blender 安装位置，只能扫描文件系统。
7. **本机 MSI 服务不可用**（`msiexec` 报 `0x80070643`），任何依赖 `.msi` 安装的路线（含 python.org 官方安装包的 `/quiet` 静默安装）都走不通，只能用 7-Zip 逐层剥离。
8. **tkinter sidecar 的 DLL 必须整包收**：`tcl86t.dll` 依赖 `zlib1.dll`，少一个就报 `DLL load failed`；而 `_tkinter.pyd` 文件名自带下划线，**别用"含不含下划线"判断是否根级二进制**。这两处已由 `tools/build_tkinter.py` 处理，改它之前先看 `DEVELOPMENT.md` 对应条目。

## 约定

- 命令一律用**绝对路径**调 `blender.exe`，不依赖 PATH
- 不改动用户的 `.blend` 工程文件——引擎/采样/分辨率/输出路径全部走命令行参数覆盖
- 注释与文档用中文；文件名用英文
- 输出产物不写进仓库（见 `.gitignore`）
- 新增"已实测"的结论时，同步更新本文件的「关键坑」和 `DEVELOPMENT.md`

## 常用命令

```bash
# 推送 —— 本机 schannel 的证书吊销检查会失败（CRYPT_E_REVOCATION_OFFLINE），
# 必须显式指定 openssl 后端，否则 push 报错
git -c http.sslBackend=openssl push origin main

# 复现 Blender 输出格式实测（生成 probes/*.log）
python tools/probe_render.py

# 重建 tkinter sidecar（本机 Python 无 tkinter）
python tools/build_tkinter.py

# 手动跑一次无头渲染（4 帧，Cycles）
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b "工程.blend" -s 1 -e 4 -a

# 强制走 OptiX 设备
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b "工程.blend" -f 1 -- --cycles-device OPTIX
```

## 详细规则

- 命令行的完整选项清单：`blender.exe --help`，其中 `--cycles-device`、`--python-exit-code`、`-E` 在渲染控制里最常用
- 实测原始日志：`probes/probe_cycles.log`、`probes/probe_eevee.log`
