# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-04（commit `8af61ca`）图形界面版可用 · GUI + 深色主题 + 自动读工程配置
> v0.3.0 详见 CHANGELOG
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

---

## 技术栈（精确版本）

- **Blender 5.2.2 LTS**，内置 Python 3.13.13，引擎 Cycles / EEVEE，渲染设备 OptiX 或 CUDA
- **宿主 Python 3.13.12**（托管版，**不含 tkinter**）——靠 `sidecar/` 提供（`tools/build_tkinter.py` 生成，`tkboot.py` 负责自举）
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
9. **输出路径必须自己替换 `####`，两边都不能交给 Blender**：`bpy.ops.render.render(write_still=True)` **不替换** `####`（实测写出一个真叫 `f_####.png` 的文件，6 帧互相覆盖）；而 `scene.render.frame_path(frame=f)` 在 filepath 里没有 `#` 时会**再追加一次帧号**，给出 `f_00010001.png`。现在的做法：`driver.output_path_for()` 手动替换 + 手动补扩展名 + `use_file_extension=False`，渲染后再 `os.path.exists` 复核。
10. **别拿 `RenderSettings.engine` 的 `enum_items` 当能力清单**：Cycles 是 addon，`-b` 启动初期 enum 里只有 `['BLENDER_EEVEE']`，但**直接赋值 `'CYCLES'` 是成功的**——enum 延迟刷新，按清单过滤会把可用引擎误判为不可用。另外引擎属性在 `scene.render.engine`，**不在 `scene.engine`**（写成后者会静默抛 AttributeError）。5.2 只认 `BLENDER_EEVEE`，不认 `BLENDER_EEVEE_NEXT`。
11. **单帧重试次数只能在收到该帧 `frame_start` 时 +1**：早先的做法是"每轮开始时给所有待渲染帧 +1"，结果崩在第 3 帧时，压根没轮到的第 4 帧也被扣光额度直接放弃——崩溃续跑直接失效。额度还要立刻落盘，否则崩了之后永远耗不尽、无限重启。
12. **读 Blender 的 stdout 要按 `\r` 切行**：采样进度是 `\r` 原地刷新的，只按 `\n` 切会让整轮进度挤成一行。现在用 `bufsize=0` + 分块 `read(256)` 再按 `\r`/`\n` 切（见 `core._pump`），既实时又不逐字节慢跑。
13. **ttk 深色主题必须先切 `clam`**：Windows 上默认主题是 `vista`（系统原生绘制），`style.configure(background=...)` 被**静默忽略** —— 只有日志区变深、输入框还是白的。配色集中在 `theme.py`，改完用 `style.lookup("TEntry","fieldbackground")` 读回实际值确认。下拉列表是 Tk 原生 Listbox，要走 `root.option_add("*TCombobox*Listbox.background", ...)`。
14. **界面里的耗时操作一律后台线程 + 事件回主线程**：tkinter 不是线程安全的，工作线程直接碰控件迟早随机崩。`gui.py` 的约定是「后台线程只 `queue.put()`，主线程 `root.after(80ms)` 取队列刷控件」；读工程配置也走 `__inspect__` 事件回主线程（见 `_handle`）。
15. **`main.py` 必须先自举再 import gui**：`import brconsole.gui` 会连带 `import tkinter`，本机 Python 没有它 —— 顺序反了就是 `ModuleNotFoundError`，永远轮不到 `tkboot` 去带 sidecar 重启。
16. **Blender 的默认输出路径 `/tmp/` 等于"没设置"**：`.blend` 里 `render.filepath` 默认就是它（Windows 上也一样），直接透传会填出一个不存在的目录；`inspect.output_template_from` 会把 `/tmp` 与 Windows 上的 `/` 开头路径都退回工程目录。

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

# 单测（89 条，不依赖 Blender）
python -m unittest discover -s tests -p "test_*.py"

# 真机冒烟：渲染 → 杀掉 Blender → 续跑 → EEVEE 切换 → 读工程配置（需要 Blender 5.2）
python tools/smoke_real_blender.py

# 界面：直接打开 / 载入工程 / 自检跑一轮模拟任务（用假 Blender）
python main.py
python main.py --gui 工程.blend
python main.py --demo

# 抓界面截图（验证布局与配色；--probe 采样像素颜色）
python tools/capture_screen.py C:\Temp\ui.png --window "blender-render-console · 无头渲染控制台" --probe "400,79;400,500"

# 只读一个工程的渲染配置（排错用）
python -m brconsole.inspect 工程.blend

# 跑一次真实渲染
python main.py 工程.blend -s 1 -e 10 -E CYCLES --samples 64 -o out/frame_####

# 复现 Blender 输出格式实测（生成 probes/*.log）
python tools/probe_render.py

# 重建 tkinter sidecar（本机 Python 无 tkinter，GUI 阶段用）
python tools/build_tkinter.py

# 手动跑一次无头渲染（4 帧，Cycles）
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b "工程.blend" -s 1 -e 4 -a

# 强制走 OptiX 设备
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b "工程.blend" -f 1 -- --cycles-device OPTIX
```

## 详细规则

- 命令行的完整选项清单：`blender.exe --help`，其中 `--cycles-device`、`--python-exit-code`、`-E` 在渲染控制里最常用
- 实测原始日志：`probes/probe_cycles.log`、`probes/probe_eevee.log`
