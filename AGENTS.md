# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-18 · v0.8.4 · 重试合并成**一个下拉**（次数同时管重启与单帧尝试，默认「一直重复」）
> （commit hash 待推送后补；上一基线：v0.8.3 收成一个「尽力渲完」勾选 —— **那一版读偏了用户意思，已被本版取代**；
> 再上：v0.8.2 场景名对不上→立刻失败 + 停止不再吃重试额度；v0.8.1 自绘 ✔ + 原生输出默认开）
> v0.7.0（过夜挂机三件套 + 版本切换）、v0.6.6（单实例锁）、v0.6.5（第二轮全量审核）、
> v0.6.4（替身分叉 / 双扩展名 / 真值判断）与更早版本见 CHANGELOG
> v0.6.2（磁盘空间预检 + 架构度量/边界检查工具）、v0.6.1、v0.6.0 见 CHANGELOG
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
32. **"崩溃后接着渲"不能靠"崩溃时打标记"**：进程被 `taskkill /F` 强杀或系统掉电时**根本来不及写文件**，任何"退出钩子里记一笔"的设计都是空谈。必须**反向设计**：点开始时默认置 `autoresume=True`（"要自动续跑"），只有**用户主动停止**或**正常渲完**才撤销/删档。于是崩溃 = 存档原封不动 = 下次开机自动续跑（实测：强杀后 `pending.json` 里 `autoresume` 仍是 True、断点 `created` 时间戳停留在首次启动、重启后从第 662 帧接着跑到 1550/4000）。
33. **存档要存"能重建任务的全部东西"，而不是 JobConfig**：`blender_exe` **不在 `JobConfig` 里**（它是 `RenderJob` 的构造参数），`resolution_percentage` / `max_frame_attempts` / `max_no_progress_rounds` 也不在断点文件里 —— 漏了任何一个，开机后重建出来的任务都会"缺胳膊少腿地按默认值跑"。所以 `taskstore` 存的是 `JobConfig.to_dict()` **加** `blender_exe`；`JOB_CONFIG_FIELDS` 加参数时必须同步，`test_core.py` 里有一条**对着实例属性核对**的测试专门防漏。
34. **开机自启只写 HKCU，且必须能识别"登记项指向别处"**：写 `HKEY_CURRENT_USER` 免提权、也只影响自己（HKLM 要管理员且影响所有用户）。但注册表存的是**绝对路径**，exe 一挪窝，开机拉起来的就变成另一个程序 —— `autostart.is_current()` 必须能比对出不一致，界面要提示"⚠ 登记的是别的位置，重新勾一次即刷新"。另外 Run 项无法指定"不弹控制台"，源码模式要用 `pythonw.exe`（否则登录后闪一个黑窗）。
35. **"界面开关不通电"是本项目最容易犯的 bug 类型**：`v_auto_resume`（「启动时自动续跑未完成任务」）曾经建了、显示了，但**全项目没有一处读它** —— 勾上/取消行为完全一样，而界面截图看不出任何异常。新增控件时必做两件事：①全局搜变量名确认**有消费方**；②问自己"我怎么**从外部观察到**这个开关产生了效果"。观察不到的开关就是没接线。
36. **会被"另一个进程、另一个时刻"使用的路径，一律在配置诞生时绝对化**：`blend` 早就 `abspath` 了，输出模板却原样留着 —— 而开机自启拉起的进程**工作目录 ≠ 用户点开始时的工作目录**（Windows 的 Run 项只规定"数据值是一条命令行"，没有任何指定工作目录的机制，见 Microsoft KB 179365）。后果不报错、只是**静默重渲**：产物和 `.render_state.json` 都漂到别处，续跑找不到断点就从头再来。`//` 前缀要先按工程目录展开再绝对化（`abspath("//out/f_####")` 会变成 `\\out\f_####` 这种 UNC 路径）。
37. **Run 项的数据值上限 260 个字符**：Microsoft KB 179365 原文 "The data value for a key is a command line no longer than 260 characters." 超了**写入本身会成功**、开机却不执行 —— "开关开着、功能没生效"是最坏的状态（用户只会以为自己没设对）。`autostart.enable()` 超限直接拒绝并提示把程序挪到更短的目录。
38. **失败与崩溃不能共用同一套自动重试策略**：判据是「进程有没有**活着**把结果报出来」，而不是「成功还是失败」。被强杀/掉电 → 压根没代码执行 → `autoresume` 保持 True → 开机续跑；而被取消 / 重试额度耗尽 / 抛异常都是**活着的进程**写出来的结果 → 撤掉自动续跑（断点保留）并把 **原因**记进存档（`auto_reason`），否则一个注定失败的任务（如场景里没有相机）会每次登录都白跑一遍。这段判据放在 `guimodel.settle_task_action()`（不碰 tkinter）就是为了能被单测钉住。
39. **"一直重启"要顺手把限次参数默认成"不限"，但**绝不能把兜底一起删掉**：只把"重启次数"放开时，`max_frame_attempts` / `max_no_progress_rounds` 里随便留一个正数，任务就会在"其实还能接着重试"的时候结束（帧被判 `exhausted` 踢出队列、或撞上 `no_progress` 刹车）——与用户选的选项正好相反。所以界面在选中"一直重启"时把两框**默认填 `0`**。但这只是**默认值、不锁控件**：`no_progress` 是内核里唯一防死循环的闸门（`test_core.py::test_no_progress_guard_stops_unlimited_mode` 专门钉着它），锁死等于把闸门删了，卡在同一帧反复崩时会一直转到有人来停。配套单测 `test_gui_model.py::TestUnlimitedRestartDefaults`，其中 `test_user_can_put_the_guard_back` 就是防"默认值被实现成强制值"的护栏。**这条规则只放在界面层**：内核若也强制，`--max-no-progress` 会被静默忽略（"参数悄悄变了"），且 CLI 与 GUI 的语义会分叉。
    **v0.8.4 起这条规则的实现点变了，但规则本身没变**：界面上那三个控件（重启下拉 + 两个数字框）合并成**一个「失败重试」下拉**，三个参数由 `guimodel.retry_plan(label)` 一次算出 —— `"一直重复，直到渲完"` → `(-1, 0, 3)`，`"3 次"` → `(3, 3, 3)`，即**选中的次数同时管重启次数与单帧尝试**。关键点是那个 3：**无进展兜底永远不跟下拉走**，因为它是"再试也没意义"的判据、不是重试次数，跟着变成不限就等于把唯一的闸门关掉。护栏单测是 `TestRetryChoices::test_no_progress_guard_never_follows_the_dropdown`（遍历每个选项，兜底都必须 == 3），另外 `test_a_number_means_the_same_number_for_every_knob` 钉住"选 N 次则两个参数都是 N"这条用户口径。
    **中间踩过一版弯路（v0.8.3）值得记**：当时把用户那句"一个参数控制所有的"读成"一个布尔控三件事"，交出去一个「尽力渲完」勾选 —— 控件是少了，但丢掉了"次数"这个档次，用户要的是"我最多重试几次"，不是"我要不要尽力"。所以**控件数量是结果不是目标**：先把用户的原话复述一遍（"选 3 次，那其他的尝试也是三次"），再决定往哪个方向减。现在"三重不限"这种组合从界面上**拼不出来**（兜底恒 3），比当年给它弹警告更彻底。细节粒度（10 次、兜底 7 轮）保留在 CLI 的 `--max-restarts / --max-frame-attempts / --max-no-progress`；`retry_label_from_config()` 负责把存档里的数折成下拉文案，预设外的数（7 次）也如实显示、不折叠 —— 折叠就等于"回填一下行为变了"。

40. **能搞到"打不开机器"的只有一条路：把系统盘写满**：Run 项是**登录之后**才由 Explorer 读的（而且安全模式不加载它 —— 除非值名前加 `*`，我们没加），所以自启项拦不住开机，渲染本身也不会。真正会把机器变成"进不去桌面"的是**输出目录落在系统盘并被写满** —— pagefile / 用户配置文件 / Temp 全都写不进去。判据收在 `diskspace.py`，两档都建立在确定性事实上（**不猜压缩率**）：水位线（系统盘 10 GiB、其它盘 2 GiB）拒绝开工；未压缩上界估算只提醒不拦。三条别踩的：①`shutil.disk_usage` 在 Windows 上**要求传入已存在的目录**，而输出目录常常是用户刚填、还没建的 → 先 `nearest_existing_dir()` 降级到最近的已存在祖先；②**取不到空间一律放行**（`None` 当"不知道"），拦不住的代价远小于误拦；③注入式探测函数**不能要求路径真实存在**，否则"别的盘""不存在的盘"这几条规则根本没法测。另外 `gui.on_start` 的拦截必须放在**存档之前**，否则会在存档里留一条注定 `drop` 的待办。

41. **架构结论必须由脚本量出来，而且第一个数字一定要怀疑**：`tools/arch_metrics.py` 解析 import 建图算 Ca/Ce/I、环、SDP 违规。写它时连踩两个坑 —— ①`from .core import X` 的候选链断了，所有文件级依赖被**静默并成包名**；②`from . import a, b, c, d, e` 一条语句只算成了第一条边，`gui` 的 Ce 从 9 缩到 5。两次都是"数字看着挺像回事、其实全错"。所以：**指标是探测器不是结论**，出表后要回代码抽查几条边对不对。另一条同样重要 —— **规则别把正确的东西也判违规**：`check_boundaries.py` 第一版把组合根 `main.py`（天然什么都依赖）和终端适配器 `cli.py`（打印就是它的职责）报成 12 处违规，这种检查不会有人看，最后会被关掉，**误报比漏报更致命**。最后：检查脚本必须有**反向用例**（`tests/test_boundaries.py` 每条规则造违规样本断言命中 + 造合规样本断言不误报），只跑正向证明不了任何事。

42. **"打桩验证"必须断言打桩真的生效，否则会得到假的"已验证"**：要验证一条测试有没有牙，常用手法是临时把被测逻辑改坏、看它是否变红。但如果那次"改坏"是用**字符串替换**做的，**锚点没匹配就会静默空转** —— 文件根本没变、测试当然全绿，于是得出"这条测试有牙"的错误结论。2026-10-05 实测踩到：给 `locate.scan_root` 的深度限制打桩时锚点前导空格数写错，替换了个寂寞，`test_depth_limit` 照样通过，差点把一条**空转的测试**当成防线报出去（复查时才发现）。规矩：替换后先 `assert s2 != s`，再 `grep` 确认桩在位，跑完恢复后再 `grep` 确认桩没了 —— **三次确认，缺一不可**。同理：`grep -c` 的结果要真的看一眼，别只看命令有没有报错。

43. **耦合指标要按"边集合"读，不能只比计数大小**：重构完对比 `Ce` 从 9 变 8，就以为"少依赖一个模块"——其实那次是**换了一条边**（去掉 `cli`、加了包根），数量根本没变。"数字变小"和"依赖变干净"是两件事。正确读法：把**边集合**打出来逐条比（`tools/arch_metrics.py` 的历史对比），或者干脆盯"某条不该有的边还在不在"。同理，指标只在**同一把尺子**下才可比 —— 解析器中途修过 bug 的话，改前改后的数字根本不是同一口径（本项目的解析器前后修了 4 个盲点，早期数字与现在的不可比）。
46. **方案选型先检索再定，"我想到的第一个做法"不等于方案**：单实例锁第一反应是"锁文件 + PID"，检索后发现那是反模式（残锁挡人 / PID 复用误判 / check-then-create 竞态），微软推荐命名互斥量（崩溃自动回收、原子创建）。确认"是真问题"的两条腿：自己的复现 + 外部佐证（Blender 官方 #37974 正是同款事故）—— 两腿都齐才动手，列了计划再按计划做。
45. **"兜底"类的文档承诺要对照实现验一遍，而且要连着下一个环节一起验**：`state.load` 的 docstring 写"状态文件坏了宁可从头来"，但类型转换写在 try 外面 —— 一半的坏文件照样抛异常；更隐蔽的是 `blend: null` 这类**能通过 load、死在下一个环节**的坏数据（`os.path.abspath(None)` 抛 TypeError），炸的位置在开机认领这条无人值守路径上，窗口版 stderr 无处可去，现象是"功能静默失效"。对策：① 写完兜底逻辑，用"**合法 JSON + 错误字段值**"实测一遍（只测半个文件这种老Case测不出）；② 无人值守路径（`root.after` 拉起的回调）必须整段兜住并**留下日志** —— 那种场景下日志是唯一的线索。
44. **"测试替身"里的常量表会跟真实现分叉，而且分叉时测试照样全绿**：`tests/fake_blender.py` 自己有一份输出扩展名表，只有 PNG/JPEG/EXR **三项**，而真 driver 有 13 项 —— 于是"TIFF 的输出文件名对不对"这件事，**用假 Blender 跑的所有测试都验不到**，打包自检用的又正是假 Blender。这类分叉不会报错、不会变红，只会在用户真用 TIFF 时以"文件名莫名多了个后缀"的形式出现。对策：**给替身与真实现之间加一条逐项相等的契约用例**（`tests/test_driver_contract.py`），改了一边没改另一边立刻红。同理，只要"同一个规则在两处各写一遍"，就该有一条测试把它们锁在一起。

47. **tkinter 里"动态增删的行"，位置必须由一处集中重排，不能拿列表长度当网格行号**：帧段列表第一版在 `_add_segment_row` 里写 `row.grid(row=len(self._seg_rows))` —— 删掉中间一段后，列表长度与"已被占用的网格行"就对不上了，接着添加的新段会**落在同一格里**：模型里明明有段 1/2/3，屏幕上只画得出段 1 和段 3（重叠的那行被盖住），而**单测全绿**（`guimodel` 里段数据完好无损）。现在位置只由 `_sync_segment_ui()` 按 `enumerate` 统一重排。教训：这类 bug 只有"数一数屏幕上到底画了几行"才发现得了 —— 断言要写成 `winfo_ismapped()` 的**行数**和 `grid_info()["row"]` 的**序列**，别只断言数据模型。

48. **`wrap="none"` 的 Text + 只有竖向滚动条 = 长行被静默裁掉**：日志区原来就是这样，而 Windows 路径动辄上百字符 —— 出错时最关键的那一行往往正好是被裁掉的那一行。改 `wrap="word"` 前先实测过才知道它够不够：本机 `tmp_probe/probe_wrap.py` 对 none/word/char 三种模式量了"显示行数"（420px 宽下 3 段文字：none=3 行＝没换行，word=5、char=5＝都换行了），也就是 **`word` 对超长的单个 token（路径）同样会在边界处断开**，不必退到 `char`。同一类问题在 Label 上是另一个开关：`wraplength`，且要跟着容器宽度动态设（`<Configure>` + `max(320, e.width-8)`）。

49. **`int(x or 1)` 会把有意义的 `0` 当成"没填"**：步长写 `int(step or 1)`，于是 `--step 0` / `步长=0` 被静默当成 1 ——"我明明写了每 5 帧"变成每帧都渲，方向恰好相反且不报错。凡是 `0` 有语义（不限、不启用）的数值参数，一律显式判 `None`/空串，再单独校验下界。同族写法 `x if x else default` 也一样危险。

50. **"同一个展开规则"必须收成一个原语，两条入口都调它**：帧号展开（`range(a, b+1, step)`）原先只在 `parse_frames` 里；界面改成帧段列表后就出现了"再写一遍"的诱惑 —— 那会让 CLI 渲的帧与界面显示的帧慢慢漂开且**不报错**。现在收成 `core.expand_segment()`，`parse_frames`（文本入口）与 `guimodel.segments_to_frames()`（段入口）都调它，并用 `test_matches_expand_segment` 把两条路锁在一起。判断"要不要收"的标准很朴素：**这条规则我能不能用一句话说清**（"闭区间 + 按步长取，起大于止就交换"能），能说清就该只有一份实现。

51. **Blender 5.x 的 `Action` 已经没有 `fcurves` 了**：4.4 起换成 slotted actions，老写法 `for fc in obj.animation_data.action.fcurves` 直接 `AttributeError: 'Action' object has no attribute 'fcurves'`（实测 5.2.2）。造测试场景时**别去改插值**——想要"每帧画面都不同"，直接**逐帧** `keyframe_insert`（`rotation_euler=(0,0,(f-1)*0.5)`），插值方式就无所谓了。另外用 `--python-expr` 建场景**必须带 `--python-exit-code 1`**：不带的话上面那个 AttributeError 会被 Blender 吞掉，只剩"文件不存在"这一个现象（AGENTS 坑 5 的原样重演）。

52. **ttk 的 image 元素不持有 Python 对象，`PhotoImage` 一被 GC，元素就画不出东西（尺寸还在、不报错）**：`style.element_create(name, "image", img_off, ("selected", img_on))` 只把 Tcl 侧的图像名记进元素，Python 的 `PhotoImage` 谁都不引用就会被回收、Tcl 图像随之 `image delete`。此时元素**照旧占着 13px 的位置**，但一个像素都不画 —— 界面上表现为"复选框凭空消失"，`reqwidth` 量出来还是正常的 19px，日志里一个字都没有。堆栈里看不出任何异常，只有截图能发现。
    接住引用的地方也踩过：最初写 `style._brc_check_images = (on, off)`，而调用方 `run_gui()` 里是裸的 `theme.apply(root)` —— **返回值没人接**，`ttk.Style` 实例出了函数就被回收，图片跟着没。现在引用挂在模块级的 `theme._IMAGE_REFS`，不依赖调用方是否留着 `Style`；`tests/test_theme.py::TestIndicatorImagesAreKeptAlive` 用替身把这条锁住（断言"图片只挂在 style 上"会失败）。
    教训：**任何交给 Tk 的图片，引用都要有明确的、比调用栈活得久的持有者**。同类陷阱还有 `root.iconphoto`、`Label(image=...)`、`Canvas.create_image`。

53. **ttk 的样式选项是"配了不生效、也不报错"，深色主题尤其容易撞上；clam 的选中标记是画出来的 ×，改颜色救不了**：`theme.py` 曾经给 `TCheckbutton` 配 `indicatorcolor` 并把选中态映射成蓝色 —— 而 clam 的指示器元素只认 `indicatorbackground/indicatorforeground/indicatorsize/indicatormargin/upperbordercolor/lowerbordercolor`（Tk 源码 `generic/ttk/ttkClamTheme.c::IndicatorElementOptions`），那行配置从写下那天起**一次都没生效过**，界面上也永远看不出来（就是 AGENTS 坑 35「开关不通电」的样式层版本）。
    而真正决定"长什么样"的是元素本身：clam 的 `CheckIndicatorElementDraw()` 在选中态用两条对角线画叉（`XDrawLine` 各画 3 遍），**Tk 里根本没有对勾这个字形**，所以"把颜色改成绿色/蓝色"只会得到一个彩色的叉。要 ✔ 只能换元素：`element_create(..., "image", ...)` + `style.layout()` 重排（见坑 52 的引用问题）。
    自查手段两个：`style.element_options(元素名)` 会列出该元素**真正支持**的选项（`theme.unsupported_options()` 就是拿它做差集）；`style.layout("TCheckbutton")` 能直接看出布局里挂的是哪个元素。

54. **clam 滚动条的"粗细"是 `-arrowsize` 决定的，名字只在有箭头时才说实话**：想给深色界面做"细杆滚动条"（VS Code 那种），第一反应是找 `-width`/`-thickness` —— 没有这个选项。实测 `arrowsize` 就是横截面尺寸：把 `uparrow/downarrow` 从 `layout()` 里去掉之后，`arrowsize=8/10/12` 直接对应控件宽 8/10/12px（原来带箭头时是 15px）。
    另外两处必须一起改，否则细杆会"糊成一团"：① `lightcolor`/`darkcolor` 要和 `background` 同色 —— clam 的滑块会画 1px 亮边 + 1px 暗边，10px 的杆上这两条棱线几乎占满宽度（旧样式看着"断成两截"就是这个）；② `gripcount=0` 关掉防滑横线，不然像个小梯子。
    还有一条使用层面的取舍：去掉箭头后**滚轮 / 拖滑块 / 点槽翻页都还在**，而箭头在 10px 杆上本来也点不准 —— "少两个按钮"不等于"少两个能力"，改之前先想清楚真正被拿走的是什么。

55. **"重置一个对象"只能有一份代码，手写清单一定会漏字段**：`guimodel.ProgressModel` 原先在 `job_start` 里手写了一份重置清单，只列了 `total/done/failed/restarts/crashes/error/phase`，漏了 `cancelled / elapsed / current_frame / stalled / eta_sec / ok / last_message`。后果不是报错，而是「停止 → 再开始」之后状态栏**整轮**都显示「已取消 · 完成 3/6 · 用时 1.9s」（1.9s 是上一轮的用时），因为 `status_text()` 里 `cancelled` 的判断排在 `running` 前面。单测全绿（没人测"第二次开始"）。
    现在默认值收在 `ProgressModel.reset()`，`__init__` 与 `job_start` 都调它 —— 以后加字段不会再漏。同类信号：**只要看到"构造里赋一遍默认值、事件里再赋一遍"，就说明这份默认值迟早会漂**。
     配套的两条：① GUI 点「开始渲染」时也要先 `reset()` —— `job_start` 要等后台线程跑起来才到（中间还有建目录、读写断点的 IO），而 `_refresh` 每 80ms 就用旧模型刷一次，不复位就会"进度条跳回上一轮的百分比再归零"；② **事件必须带足状态**：`job_start` 原先只报 `total`，界面无法知道"已完成 2/6"，只能从 0 画，于是续跑看起来像"断点没加载上"——补一个 `done` 字段就解决了。判断标准：**界面需要的每个数字，事件里都得有，不能靠猜或靠后续事件补齐**。

56. **「上游算出来了、下游没人接」是本项目最常见的缺陷形态 —— 一个字段只命中生产侧就是死字段**：`driver.activate_scene()` 做了回读校验并给出 `scene_ok`（docstring 还写着"外层还能据此把实际场景名写进日志"），而 `core._handle_json` 的 `start` 分支只转了 `scene / scene_requested`，`scene_ok` 到此终止 —— `grep -rn scene_ok brconsole/` 只命中 `driver.py` 两处。后面那条链一路滑到底：Blender 遇到不存在的场景照常渲默认场景、**退出码 0** → 界面报「全部完成」→ `settle_task_action` 把 `pending.json` 删掉。用户拿到一整套错图，连"那批图是错的"都无从知道（那行 `warn` 混在默认开启的原生输出里）。**433 条测试全绿照不到它**，因为 `test_scene_survives_to_event_stream` 只断言 `scene` 透传、不断言 `scene_ok`。
    现在的处理：`scene_ok is False` → 先 `self.cancel()`（杀子进程，别留一个还在渲错场景的 Blender 在后台）再 `raise`，由 `run()` 收成 `job_error`，存档走 `stop` 分支保留。
    自查两条：① 新加一个字段/布尔，就 grep 一遍它的**消费者**，只命中生产侧等于没做（坑 35「开关不通电」的同族）；② 测试要断言**结论**（`scene_ok=False` 会导致什么），不能只断言**标识**（`scene` 传过去了）—— 只测"数据流过"的测试会给假绿灯。

57. **「用户主动停止」和「真的失败」必须分开记账**：`frame_start` 时就 `note_attempt(+1)` 并落盘，是"崩溃必续"的前提（进程被强杀时根本没机会补记）。但**取消不是崩溃** —— 用户点停止时那一帧往往一秒都没渲完，额度却扣掉了。不退的话，单帧尝试=3 时在同一帧上连停 3 次，该帧直接 `exhausted`，续跑**跳过它**，末尾报「有帧一直失败 · 放弃 1」：少渲一帧，还把原因归到工程/显存身上（最难查的那种错误归因）。
    修法：`JobState.rollback_attempt()` + `RenderJob._inflight_frame`（`frame_start` 置、`frame_done`/`frame_error` 清、每轮 `_run_once` 开头清），**只在 `_cancel` 已置位时**回收（Ctrl+C 同样算）。
    ⚠️ 真崩溃那条路必须**保留**这次尝试，否则一个每次必崩的帧会被无限重试 —— 判断标准就一句：**这次尝试是被我们的取消杀掉的，还是它自己崩的**。给"回收额度"这类宽容逻辑加条件时，永远先问"它会不会把死循环重新打开"。

58. **一个结果载荷被多处各自解释，迟早会有出口说反话 —— 「谁优先」必须写下来**：`job_done` 同时带 `ok`（帧全在 `done` 里）和 `cancelled`（收到过取消请求），两个值在 `core` 里分别算、互不约束。最后一帧渲完的**同一瞬间**点停止，两者同时为真，于是四处下游各解释一遍：存档按 `ok` 删了、状态栏按 `cancelled` 说「已取消 · 完成 6/6」、通知标题说「渲染已取消」、`aftermath_plan` 因 `cancelled` 让用户选好的「渲完关机」**不执行**（图全渲完了，机器开了一夜）。
    统一口径是「**`ok` 优先**：渲完了就是完成了」，`status_text` / 通知标题 / CLI 结语 / `aftermath_plan` 一处不落（`settle_task_action` 本来就按 `ok` 判，别去改它）。
    判断标准：**同一个状态被 ≥2 个出口读，就必须有一句"谁优先"写在代码里**（注释 + 单测双向），不能靠"反正平常不会同时为真"。同类信号：`if A: ... elif B: ...` 里的 A/B 若可能同时成立，就要问一句"这种时候该走哪支"。

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

# 单测（436 条，不依赖 Blender；只用标准库 unittest，**别去装 pytest**）
python -m unittest discover -s tests -t tests -p "test_*.py"

# 真机冒烟 7 场景：渲染 → 杀掉 Blender → 续跑 → EEVEE 切换 → 读工程配置 → 「一直重启」A/B → 多场景
#              → **多段不连续帧**（-f 1-3x2,5；比三张图的 sha256，证明真的跳着渲了）（需要 Blender 5.2）
python tools/smoke_real_blender.py
python tools/smoke_real_blender.py --only 7     # 只跑多段不连续帧那一场

# 打包成两个单文件 exe（产出后自动跑 5 项自检：--help / 图标逐像素比对 / 版本资源 / 内置假 Blender / 完整 core 流水线）
python tools/build_exe.py --both

# 重新生成应用图标；**改完图标先扫一眼跨尺寸对照图再打包**（16px 才是任务栏里看到的那个）
python tools/make_icon.py
python tools/make_icon.py --sheet sheet.png

# 界面：直接打开 / 载入工程 / 自检跑一轮模拟任务（用假 Blender）
# `--demo` 的默认示例就是**两段不连续帧**（1-4 + 9-10，共 6 帧）—— 顺带验证段列表真的按段逐帧渲
python main.py
python main.py --gui 工程.blend
python main.py --demo

# 开机自启模式（自启项用的就是这条）：认领未完成任务，有就自动续跑
python main.py --autostart
# 手动查看/清掉待办任务存档（排错用；存档在 %LOCALAPPDATA%\blender-render-console\pending.json）
type "%LOCALAPPDATA%\blender-render-console\pending.json"

# 抓界面截图（验证布局与配色；--probe 采样像素颜色，--list 列窗口标题）
python tools/capture_screen.py --list
python tools/capture_screen.py C:\Temp\ui.png --window "blender-render-console" --probe "400,79;400,500"

# 帧段列表的真机核对（20 项：增段/删段/段号重排/行不重叠/回显/非法段定位/日志折行 + 截图）
# ⚠️ 这两个探针在**会话工作区的 tmp_probe/**，不在仓库里；换机后按需重建
python ..\tmp_probe\gui_segments.py
# Tk 三种 wrap 模式的实测对照（none / word / char）
python ..\tmp_probe\probe_wrap.py

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
