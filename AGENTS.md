# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-11-18 · `aa50c1a` · 规范 2.1.4 拆分：59 条关键坑迁入 `rules/关键坑.md`，AGENTS 226→125 行
> （本机无 git，经 Git Data API 推送，commit `fc967c5`。上一基线：v0.8.4 重试合并成**一个下拉**（次数同时管重启与单帧尝试，默认「一直重复」）；
> 再上：v0.8.3 收成一个「尽力渲完」勾选 —— **那一版读偏了用户意思，已被 v0.8.4 取代**；
> v0.8.2 场景名对不上→立刻失败 + 停止不再吃重试额度；v0.8.1 自绘 ✔ + 原生输出默认开）
> v0.7.0（过夜挂机三件套 + 版本切换）、v0.6.6（单实例锁）、v0.6.5（第二轮全量审核）、
> v0.6.4（替身分叉 / 双扩展名 / 真值判断）与更早版本见 CHANGELOG
> v0.6.2（磁盘空间预检 + 架构度量/边界检查工具）、v0.6.1、v0.6.0 见 CHANGELOG
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本
>
> 生成文件，勿手改。改规则请改 `schema/agents.template.md`，再运行 `python tools/repo-kit/generate.py --root . --write`。

---

## 技术栈（精确版本）

- **Blender 5.2.2 LTS**，内置 Python 3.13.13，引擎 Cycles / EEVEE，渲染设备 OptiX 或 CUDA
- **宿主 Python 3.13.12**（托管版，**不含 tkinter**）——靠 `sidecar/` 提供（`tools/build_tkinter.py` 生成，`tkboot.py` 负责自举）
- 交付产物：PyInstaller **单文件** exe（`dist/blender-render-console.exe` 界面版 / `dist/brc.exe` 命令行版）
- **图标**：`assets/app.ico`（`tools/make_icon.py` 纯标准库生成，**不需要 Pillow**）
- **版本号唯一来源**：`brconsole/__init__.py` 的 `__version__`（打包时现读，别在别处再写一份）
- 平台：Windows（bash 走 Git Bash）

## 关键坑（最高频 8 条；完整 59 条见 @rules/关键坑.md）

1. **Blender 5.2 的进度输出格式与 4.x 完全不同**：网上 4.x 的 `Fra:1 Mem:... | Remaining:...` 正则匹配不到任何东西。任何解析改动先跑 `tools/probe_render.py` 对照 `probes/` 的真实样本。
2. **必须加 `--python-exit-code 1`**：否则 Blender 吞掉脚本异常并返回 0，外层误判"渲染成功"。
3. **输出路径自己替换 `####`**：`write_still=True` 不替换（6 帧写进同一个名字互相覆盖），`frame_path(frame=f)` 在无 `#` 时还会再追加帧号；渲染后 `os.path.exists` 复核。
4. **单帧重试额度只在收到该帧 `frame_start` 时 +1，并立刻落盘**：早先"每轮给所有待渲染帧 +1"会让崩溃续跑直接失效（没轮到的帧被扣光额度）。
5. **"崩溃后接着渲"要反向设计**：强杀/掉电来不及写文件，`autoresume` 默认 True，只有用户主动停止或正常渲完才撤销。
6. **界面开关必须能"从外部观察到效果"**：建了控件却没人读（坑 35）是本项目最常见的 bug；新增控件先 grep 消费方。
7. **上游算出来、下游没人接 = 死字段**（坑 56）：grep 只命中生产侧等于没做；测试要断言**结论**，不能只断言**标识**。
8. **测试替身与真实现的常量表会分叉，且分叉时全绿**（坑 44）：需要一条"逐项相等"的契约用例把它们锁在一起。

> 完整清单（59 条：ttk 样式 / 打包 / exe / 续跑 / 输出格式位数…）见 @rules/关键坑.md。

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

# 单测（450 条，不依赖 Blender；只用标准库 unittest，**别去装 pytest**）
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

# 复现"每种格式实际接受哪些位数"（跑在 Blender 里，输出一行 ##PROBE## JSON）
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b -P tools/probe_depth.py

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

## 当前规模

- 页面：4（critical=0）
- 按 type：reference=4
- 词表版本：2.7.0
