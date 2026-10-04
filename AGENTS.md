# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-04（commit `3cc9fea`）v0.5.0 · 多场景选择（界面下拉 / `-S`）+ 单文件 exe
> v0.5.0 详见 CHANGELOG
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

---

## 技术栈（精确版本）

- **Blender 5.2.2 LTS**，内置 Python 3.13.13，引擎 Cycles / EEVEE，渲染设备 OptiX 或 CUDA
- **宿主 Python 3.13.12**（托管版，**不含 tkinter**）——靠 `sidecar/` 提供（`tools/build_tkinter.py` 生成，`tkboot.py` 负责自举）
- 交付产物：PyInstaller **单文件** exe（`dist/blender-render-console.exe` 界面版 / `dist/brc.exe` 命令行版）
- **图标**：`assets/app.ico`（`tools/make_icon.py` 纯标准库生成，**不需要 Pillow**）
- **版本号唯一来源**：`brconsole/__init__.py` 的 `__version__`（打包时现读，别在别处再写一份）
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
17. **打包后 `.py` 源码不在磁盘上**：PyInstaller 把模块收进 PYZ 归档，`_MEIPASS/brconsole/driver.py` **不存在**（只有数据文件才落地）。所以"读自己的源码来注入"必须把那份 `.py` 当**数据文件**另带一份（本项目放 `_brc/py/driver.py`，见 `core.read_driver_source`）。`inspect.py` 因为把脚本写成字符串字面量才幸免。
18. **PyInstaller `datas` 的元组顺序是 `(目标名, 源路径, 类型)`**：写反了**构建照样成功、TOC 里也有记录**，但运行时文件不落地。所以"打包成功"不等于"产物可用"，`tools/build_exe.py` 默认会跑一遍自检（含完整 core 流水线）。
19. **exe 里带 tkinter 要整棵 `tcl` 目录**：只带 `tcl8.6`/`tk8.6` 会缺 `dde1.4`/`reg1.3`/`tcl8`（Tcl 的 `auto_path` 是 `[file dirname $tcl_library]`），Tk 会在初始化中途报错。另外**必须赋值覆盖 `TCL_LIBRARY`**（本机系统里另有一个软件设的 Tcl 8.6.12，沿用就 `version conflict`），并用 `os.add_dll_directory` + `sys.path` 挂上 `_brc/DLLs`。
20. **小图标不能靠等比缩放**：把 256px 那张缩到 16px，圆角会被抗锯齿啃掉、整块糊成"一个圆"，三角只剩几个点 —— 而任务栏/资源管理器里显示的**正是** 16px。`tools/make_icon.py` 的 `profile()` 按尺寸分档（≤20 / ≤28 / ≤40 / ≥48），改完图标**必须 `--sheet` 看跨尺寸对照图**再打包。
21. **ctypes 调 Win32 必须先设 `argtypes`**：默认签名是 `c_int`，64 位下 HBITMAP/HICON 这类句柄会被截断，报 `ArgumentError: OverflowError: int too long to convert`（看着像"参数传错"，实为没声明签名）。本项目 `tools/build_exe.py::_init_gdi` 与 `tools/capture_screen.py` 都是这个套路。
22. **exe 图标 / 窗口图标是两回事**：`icon=` 写进 PE 资源只影响 exe 本身；**Tk 窗口不会继承**，不额外调 `iconbitmap` 就顶着 Tk 自带的羽毛。窗口图标要另带一份数据文件（`_brc/assets/app.ico`），由 `gui.icon_path()` 定位。
23. **控制台符号要挑 cp936 编得出来的**：`✗`(U+2717) / `✓`(U+2713) / `⚠`(U+26A0) **不在 cp936 里**，Windows 控制台 exe 的 stdout 写它们会抛 `UnicodeEncodeError`；CLI 里统一用 `×` / `※`（`→` `·` 这些是安全的）。再兜一层 `reconfigure(errors="replace")`。
24. **静默吞异常的地方必须留痕**：`core.emit()` 曾写 `except Exception: pass`，把上面那个编码异常一起吞了，导致渲染失败时**一个字的错误信息都没有、进程只返回 1**，非常难查。现在首次回调异常会记进 `result["reporter_error"]`，CLI 在失败时兜底再打一次。
25. **`--windowed` 的 exe 没有可用的 stderr**：界面起不来时它表现得像"进程活着但没窗口"。排查一律**先切 `console=True` 的那个 exe**（本项目就是 `dist/brc.exe`）拿 traceback。
26. **`.bat` 可以直接当 `subprocess` 的 `args[0]`**（CreateProcess 会自己拉 cmd.exe），退出码与参数原样传递 —— 用来做"确定性地崩"的启动器很方便。但**转交路径必须用反斜杠**（`C:/...` 会找不到而返回 1），且批处理里读计数器别用 `set /p`（会吃进行尾 CR），要用 `for /f "usebackq delims="`。
27. **跨进程的时序断言要能证伪**：「监测到新帧就 kill 掉 Blender 数崩溃次数」实测在 5~7 之间浮动（有一次 kill 打到了已退出的 PID）。要么把随机性消除，要么改成 A/B 对照实验（本项目：同一份"连崩 6 次"启动器，`--max-restarts 5` 必须放弃且 0 帧产出、`unlimited` 必须渲完 12 帧）。
28. **Win32 的资源 API 只认绝对路径**：`GetFileVersionInfoSizeW` 传相对路径**返回 0**（不报错），于是 `_exe_file_version()` 静默返回 `None`，现象是"版本资源没写进 exe"，实际是路径没规范化。凡是读 PE 资源（版本 / 图标）的函数，入口一律先 `os.path.abspath()`。
29. **指定场景必须回读校验**：`blender -b x.blend -S 不存在的场景` 只往 stdout 打一行英文 `Can't find scene: 'xx'`，**退出码仍是 0**，然后照常用默认场景渲染（实测 5.2.2）—— 选错场景会静默渲出一整套错图。所以 `driver.activate_scene()` 自己切（`bpy.context.window.scene = target`，实测 `-b` 下 window **不是** None，切完渲染出的就是目标场景）+ 回读校验 + 中文告警；界面还在按下「开始渲染」前拦一道（读到的场景列表里没有这个名字就报错）。
30. **`//` 是"相对 .blend 目录"，不是绝对路径**：Blender 的**默认**输出路径就长这样，它和 `/tmp` 一样以 `/` 开头，会被 `output_template_from()` 里"Windows 上以 `/` 开头 = 不存在"那条规则一起丢掉 → 输出模板永远退化成 `工程名_####`，多场景之间也看不出区别。判断之前先把 `//xxx` 展开成 `<工程目录>/xxx`。
31. **界面下拉别维护"显示值 / 实际值"两张表**：本项目曾同时有 `RESTART_CHOICES`（中文文案 → 值）和 `RESTART_VALUES`（裸值），界面写的是后者 → 下拉框里直接显示 `1/3/5/10/unlimited`，英文 `unlimited` 露在界面上，而中文那套**从没被任何控件引用**（死代码）。现在下拉只放中文文案，交给 `core.parse_restart_limit` 解析（`"5 次（默认）"`→5、`"一直重启，直到全部渲完"`→不限）。能用同一个字符串既显示又解析，就别搞映射层。

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

# 单测（126 条，不依赖 Blender）
python -m unittest discover -s tests -t tests -p "test_*.py"

# 真机冒烟 6 场景：渲染 → 杀掉 Blender → 续跑 → EEVEE 切换 → 读工程配置 → 「一直重启」A/B → 多场景（需要 Blender 5.2）
python tools/smoke_real_blender.py

# 打包成两个单文件 exe（产出后自动跑 5 项自检：--help / 图标逐像素比对 / 版本资源 / 内置假 Blender / 完整 core 流水线）
python tools/build_exe.py --both

# 重新生成应用图标；**改完图标先扫一眼跨尺寸对照图再打包**（16px 才是任务栏里看到的那个）
python tools/make_icon.py
python tools/make_icon.py --sheet sheet.png

# 界面：直接打开 / 载入工程 / 自检跑一轮模拟任务（用假 Blender）
python main.py
python main.py --gui 工程.blend
python main.py --demo

# 抓界面截图（验证布局与配色；--probe 采样像素颜色，--list 列窗口标题）
python tools/capture_screen.py --list
python tools/capture_screen.py C:\Temp\ui.png --window "blender-render-console" --probe "400,79;400,500"

# 只读一个工程的渲染配置（排错用）
python -m brconsole.inspect 工程.blend

# 跑一次真实渲染
python main.py 工程.blend -s 1 -e 10 -E CYCLES --samples 64 -o out/frame_####

# 工程有多个场景时，指定要渲哪个场景（不给 = 用工程里激活的那个）
python main.py 工程.blend -S 室内场景 -s 1 -e 10 -o out/frame_####

# 一直重启，直到全部渲完（带 3 轮无进展兜底）
python main.py 工程.blend -s 1 -e 240 -o out/frame_#### --max-restarts unlimited --max-no-progress 3

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
