# 能力清单（走查步骤 1 产出）

> 盘点方式：只认**程序里真实存在的入口**（GUI 控件、CLI 参数、模块函数），不认 README 里的说法。
> 每条能力给 C-ID，供 `scenarios.md` 的覆盖矩阵引用。
> 基线：v0.8.1，`brconsole/` 共 6075 行 / 20 模块。
> 盘点日期：2026-10-17

## 0. 入口层

| C-ID | 能力 | 真实入口 | 证据 |
|---|---|---|---|
| C-01 | 命令行渲染（无界面） | `main.py <blend> -s -e ...` | `main.py:116` → `cli_main`；`brconsole/cli.py:40-69` |
| C-02 | 图形界面启动 | `main.py` 无参 / `--gui [blend]` | `main.py:23` `GUI_FLAGS` |
| C-03 | 开机自启认领未完成任务 | `main.py --autostart` | `main.py:101` → `gui.try_autostart_resume`（`gui.py:941`） |
| C-04 | 自检 demo（假 Blender，不吃 GPU） | `main.py --demo` | `main.py:73` `_demo_cmd_factory` |
| C-05 | 单实例互斥（第二次启动聚焦已有窗口） | 隐式 | `applock.py:35` `acquire` / `applock.py:62` `focus_window` |

## 1. 任务表单（GUI「任务」区）

| C-ID | 能力 | 真实入口 | 证据 |
|---|---|---|---|
| C-10 | 选工程 `.blend` | 「浏览…」按钮 | `gui.py:216` |
| C-11 | 自动探测 `blender.exe` | 「自动探测」按钮 / `--deep-scan` 深扫便携版 | `gui.py:234`、`cli.py:57` |
| C-12 | 多 Blender 版本下拉切换 | 版本下拉 | `gui.py:548` `_sync_blender_versions` |
| C-13 | 读取工程配置（引擎/采样/分辨率/帧范围/输出） | 「读取工程配置」 | `gui.py:383`、`gui.py:609` `on_inspect` |
| C-14 | 场景下拉（多场景切换后按该场景重填全表） | 场景下拉 | `gui.py:220-226`、`gui.py:722` `on_scene_change` |
| C-15 | 帧范围**多段**（每段独立步长） | 「＋ 添加一段」/ 每行「✕」 | `gui.py:254`、`gui.py:466`、`gui.py:519-535` |
| C-16 | 引擎 / 采样数 | 引擎下拉、采样输入 | `gui.py:267-272` |
| C-17 | 设备 / 宽高 / 分辨率百分比 / 输出格式 | 一行四个控件 | `gui.py:278-289` |
| C-18 | 输出模板 + 选择目录 | 输入框 + 「选择目录」 | `gui.py:294-297` |
| C-19 | 从断点续跑开关（默认勾） | 复选项 | `gui.py:303` |
| C-20 | 崩溃后重启次数（默认「一直重启」） | 下拉 | `gui.py:305`、`guimodel.py:39-41` |
| C-21 | 连续无进展轮数（0=不限制） | 输入框 | `gui.py:327-330` |
| C-22 | 单帧最多尝试次数（0=不限） | 输入框 | `gui.py:334-337` |
| C-23 | 显示 Blender 原生输出（**默认开**，同类进度行自动折叠） | 复选项 | `gui.py:342`、`guimodel.py:784` `NativeLineFilter` |
| C-24 | 任务结束后关机 / 睡眠 | 下拉 | `gui.py:367`、`guimodel.py:411` `aftermath_plan` |
| C-25 | 开机自动启动（登记自启项） | 复选项 | `gui.py:349`、`gui.py:829` `on_toggle_autostart` |
| C-26 | 启动时自动续跑未完成任务 | 复选项 | `gui.py:357`、`gui.py:840` |

## 2. 执行内核

| C-ID | 能力 | 真实入口 | 证据 |
|---|---|---|---|
| C-30 | 开始渲染（校验 → 磁盘预检 → 存档 → 起后台线程） | 「开始渲染」 | `gui.py:1004` `on_start` |
| C-31 | 停止（杀子进程树 + 撤自动续跑标记，断点保留） | 「停止」 | `gui.py:1087` `on_stop`、`core.py:695` |
| C-32 | 断点续跑（签名匹配才续；不匹配则另存旧档从头来） | 自动 | `state.py:22` `task_signature`、`core.py:432` |
| C-33 | 崩溃自动重启守护循环 | 自动 | `core.py:621-679` |
| C-34 | 无进展护栏（连续 N 轮零推进 → 停） | 自动 | `core.py:664-674` |
| C-35 | 单帧重试额度与「放弃」判定 | 自动 | `state.py:152` `remaining`、`state.py:171` `exhausted` |
| C-36 | 双通道进度（`##PROG##` 精确行 + Blender 原生行兜底） | 自动 | `core.py:488-544`、`parser.py` |
| C-37 | ETA 自算（不用 Blender 的 Remaining） | 自动 | `eta.py`、`core.py:523` |
| C-38 | 卡顿提示（静默 ≥60s 报「还在干活」+ 阶段名） | 自动 | `core.py:745`、`DEFAULT_STALL_AFTER` |
| C-39 | 磁盘空间预检（block 拦下 / warn 只提示） | 自动 | `gui.py:1028`、`taskstore.py:189` |
| C-40 | 防睡眠守卫（渲染期间宣告系统忙） | 自动 | `core.py:704`、`power.py:33` |

## 3. 进度与日志 UI

| C-ID | 能力 | 真实入口 | 证据 |
|---|---|---|---|
| C-50 | 进度条 + 百分比 | 进度区 | `gui.py:411`、`gui.py:1291` `_refresh` |
| C-51 | 状态栏文案（运行/取消/未完成/完成） | 进度区标签 | `guimodel.py:728` `status_text` |
| C-52 | 断点状态标签（已完成 N/M 帧 / 无） | 底部状态标签 | `gui.py:1299` `_refresh_state_label` |
| C-53 | 日志区（折叠 + 行数上限 + 丢弃计数显式声明） | 日志文本框 | `gui.py:1271` `_flush_log`、`guimodel.py:864` `LogModel` |
| C-54 | 日志滚动条（细杆无箭头 + 空闲时隐身） | 日志区右侧 | `theme.py` `install_scrollbar`、`gui.py:792` `_on_log_scroll` |
| C-55 | 清空日志 | 「清空日志」 | `gui.py:785` |
| C-56 | 清空断点 | 「清空断点」 | `gui.py:768` `clear_state` |
| C-57 | 打开输出目录 | 「打开输出目录」 | `gui.py:754` |

## 4. 收尾与存档

| C-ID | 能力 | 真实入口 | 证据 |
|---|---|---|---|
| C-60 | 渲完/出错通知（提示音 + 任务栏闪烁 + toast，降级可用） | 自动 | `gui.py:1165` `_finish_notify`、`notify.py:93` |
| C-61 | 关机/睡眠 60 秒倒计时（可取消；新任务开始会撤销） | 自动 | `gui.py:1203` `_countdown`、`gui.py:1256` |
| C-62 | 待办存档处置（渲完删档 / 失败与取消撤自动续跑但保断点） | 自动 | `gui.py:1154` `_settle_task`、`guimodel.py:379` `settle_task_action` |
| C-63 | 跑着关窗 → 确认 → 视为主动停止 | 窗口关闭 | `gui.py:1099` `on_close` |

## 5. 状态机与数据契约（走查要依赖，不是用户可见能力）

| 名字 | 归属 | 语义 |
|---|---|---|
| `pending.json` | `taskstore` | 任务**是什么**（`autoresume` 默认 True，只有主动停止才写 False） |
| `.render_state.json` | `state.JobState` | 任务**跑到哪了**（每渲完一帧原子落盘） |
| `resume_decision()` | `taskstore.py:195` | 开机时 `run` / `prompt` / `done` / `drop` / `none` 五分支 |
| `settle_task_action()` | `guimodel.py:379` | 一轮跑完 `clear` / `stop` |
| `restart_limits_plan()` | `guimodel.py:90` | 「重启次数 ↔ 单帧尝试 ↔ 无进展轮数」三值联动 |
| `JobConfig.signature()` | `core.py:274` | 四个字段（blend / output / frames / scene）决定是否同一任务 |

## 6. 明确**不存在**的能力（素材不得出现）

- ❌ 选择**相机**（`inspect.py:78` 只把相机名读出来**显示**，不进 `JobConfig`、不传给 driver）
- ❌ 一次任务跑**多个场景**（`JobConfig.scene` 是单值）
- ❌ 任务**队列**（`taskstore` 刻意只保留一个待办任务，见 `taskstore.py:57`）
- ❌ 渲中改参数（表单在渲染期间不参与，改了下一次才生效）
- ❌ 渲染预览 / 缩略图
- ❌ 帧级断点以外的「重渲某一帧」按钮（只能靠清空断点重来）
