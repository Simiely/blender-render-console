# 33 条链路走查报告（第 1 轮）

> 走查对象：`blender-render-console` v0.8.1（工作区未提交改动状态）
> 素材：`docs/walkthrough/scenarios.md`（S-01 ~ S-33）
> 能力清单：`docs/walkthrough/capabilities.md`（C-01 ~ C-63）
> 走查方式：深度链 21 条（逐层读代码）、快速链 12 条（grep 确认）
> 回归基线：`Ran 433 tests ... OK (skipped=1)`
> 日期：2026-10-17

## 总体结论

**33 条链路通 27 条，6 个问题（2 个真缺陷、3 个 UX、1 个极低），0 处素材与程序不符。**

数据层（`state.py` / `taskstore.py`）扎实：崩溃续跑的反向设计、签名单点归属、存档原子落盘、
`drop` 不删档这四处都经得起推敲。问题集中在**两个薄弱的接缝**：

1. **driver 已经算出来的结论没人接**（`scene_ok` 被丢弃）→ 静默渲错场景还清存档；
2. **「用户主动停止」与「真的失败」没有区分**（重试额度、取消 vs 完成的口径）→ 归因错误。

## 问题清单

| 编号 | 级别 | 问题 | 位置 | 受影响链路 | 风险分 L×I | 影响 |
|---|---|---|---|---|---|---|
| P-W1 | 🔴 真缺陷 | `driver` 把「场景名对不上」算出来了（`scene_ok=False`），但 `core` 转事件时**丢掉这个字段**，全项目无一处读它。结果：渲的是错的场景、退出码 0、界面报「全部完成」、`settle_task_action` 把待办存档**删掉** | `core.py:491-494`（丢字段）<br>`driver.py:121-123`、`142-143`（只 warn）<br>`cli.py:207-295`（CLI 零校验）<br>`gui.py:1154-1163`（清档） | S-22 S-33 S-12 | 3×5=**15** | 一整套错图，且断点被清、无法追溯；CLI 返回 0 让脚本误判成功 |
| P-W2 | 🔴 真缺陷 | 「用户点停止」会**吃掉当前帧一次重试额度**：`frame_start` 时立刻 `note_attempt(+1)` 并落盘，取消路径不回收。单帧尝试=3 时，在同一帧上连停 3 次 → 该帧 `exhausted` → 续跑**跳过它**并报「有帧一直失败/放弃 1」 | `core.py:504-512`（先记账）<br>`state.py:166`（额度到即踢出队列）<br>`core.py:688`（收尾无回滚） | S-30 S-13 | 4×4=**16** | 少渲一帧且告警归因错误（看起来像工程/显存问题）；重试额度被用户自己的操作耗掉 |
| P-W3 | 🟡 UX | 最后一帧完成与「停止」撞在同一窗口时，`cancelled` 与 `ok` 同时为真，四处口径不一致：存档按 `ok` 删、状态栏按 `cancelled` 说「已取消 · 完成 6/6」、通知标题说「渲染已取消」、`aftermath_plan` 因 `cancelled` **不关机** | `core.py:688-689`（两值独立）<br>`guimodel.py:731`（cancelled 判在 finished 前）<br>`gui.py:1175`<br>`guimodel.py:425` | S-18 S-20 S-31 | 3×3=9 | 用户选了「渲完关机」，实际渲完了却不关机；文案自相矛盾 |
| P-W4 | 🟡 UX | 三个「不限」可以同时放开（重启=一直 / 单帧尝试=0 / 无进展=0）→ **没有任何终止条件**。界面把 0 宣传成「不限制」，用户照着填就得到一个只能人肉点停止的循环 | `guimodel.py:582-588`（只拦负数）<br>`core.py:671`（`>0` 才生效） | S-07 | 3×3=9 | 每 2 秒起一次 Blender 却永远不结束；无人值守时会一直空烧 GPU |
| P-W5 | 🟡 UX | 「断点：已完成 N/M 帧」**不校验任务签名**。同一个输出目录换场景/改帧范围后，标签仍显示上一个任务的进度，点开始却从 0 渲 —— 正是这个模块注释里说要防的「骗人」 | `guimodel.py:976-984`<br>`gui.py:1299-1305` | S-16 S-03 | 3×2=6 | 用户以为会续跑，实际从头来 |
| P-W6 | ⚪ 极低 | `on_start` 里 `_cancel_pending_countdown()` 排在 blender 路径校验之前：配置错误（缺 blender）也会撤掉已经挂起的关机倒计时 | `gui.py:1012` vs `1014-1019` | S-20 | 1×2=2 | 撤了倒计时但没开始新任务 |

### P-W1 的直接证据

```
$ grep -rn "scene_ok" brconsole/ tests/
brconsole/driver.py:302:    sc, scene_ok = activate_scene(requested)
brconsole/driver.py:306:         scene=sc.name, scene_requested=requested, scene_ok=scene_ok,
# ← core.py 的 start 分支（491-494）只转 scene / scene_requested，scene_ok 到此终止
```

`driver.activate_scene` 的 docstring 写着「对不上就明确告警，**外层还能据此把实际场景名写进日志**」——
这个契约的外层实现从未落地。而 `tests/test_core.py:492` 只断言 `scene` 透传、不断言 `scene_ok`，
所以 433 条测试全绿也照不到这个洞。

### P-W2 的可复现路径

1. 重启下拉选「3 次」（两个限次框回到 3）；
2. 在第 5 帧渲染中连点「开始 → 停止」三轮；
3. 重新开始 → 日志出现「✓ 帧 4 完成」直接跳到帧 6，末尾「放弃 1」。

用户视角：我只是停了几次，为什么帧 5 被放弃了？归因完全错误。

## 覆盖矩阵结论

- 9 个模块行、7 个有效列全部非空，无整行/整列空白。
- L4 状态推进 10 条、L5 并发 3 条、L8 边界 6 条、L9 故障注入 6 条 —— 均达标。
- L6 离线整列空：程序零网络依赖，已写理由。
- L7 权限矩阵不适用：单用户单机，改用「外部运行环境差异」变体（S-23 / S-24 / S-15）。
- **盲区已补**：第一版素材漏了「无人值守路径」（`--autostart` 拉起的整条链），
  探索会话补上后立刻捞出 P-W1 的最严重形态。

## 探索会话（SBTM，1 个 charter）

**Charter**：`Explore 开机自启续跑 with 存档 + 假 Blender，to discover 无人值守路径上的静默失败与脏残留`

- **Past**：走了 `_adopt_pending` 的五个分支（run / prompt / done / drop / none），
  并与 `on_stop`、`on_close`、`_settle_task` 交叉。
- **Results**：捞到 P-W1 的无人值守形态 —— 工程里场景被改名后，开机自动续跑会渲默认场景、
  报「全部完成」、清掉存档；用户第二天看到的是一整套错图，且**没有任何一条醒目提示**（
  `warn` 混在默认开启的原生输出里）。另发现 P-W5（断点标签说谎）。
- **Obstacles**：沙箱内无法验证注册表自启项的真实拉起，用 `--autostart` 直接模拟。
- **Outlook**：下一个 charter 建议 `Explore 停止/继续 边界 with 断点文件，to discover 重试额度与状态残留` —— P-W2 就是从这条线索挖出来的。
- **Feelings**：数据层信心高；**错误上报通道信心中等**（算出来了却没人接，是本轮最典型的缺陷形态）。

## 走查验证的良好点

不是空谈，这些是程序真正扎实的地方：

1. **反向设计的续跑标记**（`taskstore.py:15-20`）：崩溃来不及写文件，所以默认 True、只有主动停止才置 False。设计意图与实现完全一致，没有"正向打标记"的漏洞。
2. **存档赶在起线程之前**（`gui.py:1036-1038`）：把「已开跑但未存档」的窗口压到最小。
3. **磁盘预检拦在存档之前**（`gui.py:1026-1038`）：被拦下的任务不会留下一个下次开机以 `drop` 态空跑一轮的脏存档 —— 这个顺序是刻意排的，注释也写了原因。
4. **`drop` 分支绝不删档**（`taskstore.py:213-216`）：U 盘拔了、blender 换位置都能恢复。
5. **签名单点归属**（`state.task_signature`）：`JobState.signature()` / `taskstore.progress_of()` / `JobConfig.signature()` 三处共用一份判据，没有各写一遍。
6. **`ProgressModel.reset()` 单点默认值**：`__init__` 与 `job_start` 共用，加字段不会再漏（这条正是上一轮修掉的「停止→再开始状态栏卡在已取消」）。
7. **决策逻辑全部下沉**：`settle_task_action` / `aftermath_plan` / `restart_limits_plan` / `restore_limits_backup` 都放在不碰 tkinter 的 `guimodel`，每个都有单测钉住。
8. **`clear_state` 双重保护**：按钮渲染中 `disabled` + 方法内再查 `prog.running`。
9. **单实例用命名互斥量**而非锁文件：崩溃自动回收，不会留残锁把用户挡在门外。
10. **日志丢弃显式声明**：`LogModel.dropped` → 「已丢弃最早的 N 行」，不静默吞证据。

## 结论三问

**1. 链路逻辑通顺吗？**
主干通顺。断裂只在一处：**driver 的结论到不了界面**（P-W1）。其余问题都是同一段逻辑在
两个出口（界面 / 命令行、取消 / 失败）上给出了不同答案。

**2. 是补丁叠补丁吗？**
不是。每个概念都有单点归属：任务定义（`pending.json`）、进度（`.render_state.json`）、
回填（`FormModel`）、决策（`guimodel` 的四个纯函数）。P-W3 是唯一的"两处各自判断"——
`cancelled` 和 `ok` 由同一个 `job_done` 载荷带出，却被四个下游各解释一遍。

**3. 会无法操作/出 bug 吗？**
- **会渲出错图且不自知**（P-W1，无人值守时最危险）；
- **会少渲一帧并错误归因**（P-W2）；
- 不会出现"卡死无法操作"：任何循环都能被「停止」打断。

## 建议处理（按风险分排序）

| 顺序 | 编号 | 动作 | 状态 |
|---|---|---|---|
| ① | P-W1 | `core` 透传 `scene_ok`；`scene_ok is False` 时**立刻杀子进程并以明确原因失败**（存档走 `stop` 保留，可恢复）；`event_line` 增加醒目提示；补 3 条单测 | ✅ 已修 |
| ② | P-W2 | 取消路径**回滚一次 attempt**（只在「用户取消 + 有在渲帧」时回滚，崩溃路径不动，保住"崩溃必续"）；补单测 | ✅ 已修 |
| ③ | P-W3 | `ok` 优先于 `cancelled`：`settle`/状态栏/通知/善后统一按「渲完了就算完成」判定；补单测 | ✅ 已修 |
| ④ | P-W4 | 三个「不限」同时成立时给**显式警告**（不硬拦，用户确实可能要） | ✅ 已修（**v0.8.3 起改为不可达**，见下） |
| ⑤ | P-W5 | `state_summary` 接收签名，不匹配时标签显示「断点：属于别的任务」 | ⏸ 本轮不做（风险分 6 = 可选，见残余风险） |
| ⑥ | P-W6 | `_cancel_pending_countdown()` 挪到所有校验通过之后 | ✅ 已修 |

## 修复闭环（步骤 5）

### 改了什么

| 文件 | 改动 |
|---|---|
| `driver.py` | `start` 事件补 `scenes`（失败信息要能列出真实场景名） |
| `core.py` | 新增 `scene_missing_error()`；`start` 分支：`scene_ok is False` → `cancel()` + `raise`；新增 `_inflight_frame` 与 `_drop_inflight_attempt()`（收尾 + `KeyboardInterrupt` 两处调用）；`_run_once` 开头清 `_inflight_frame` |
| `state.py` | 新增 `rollback_attempt()` |
| `guimodel.py` | `status_text()` 的 `cancelled` 不再是绝对优先；`aftermath_plan()` 去掉 `cancelled` 一票否决；`to_config()` 三不限警告 + 抽出 `eff_attempts / eff_no_progress` |
| `gui.py` | 通知标题 `ok` 优先；`_cancel_pending_countdown()` 挪到所有校验之后 |
| `cli.py` | `job_done` 结语 `ok` 优先（与界面同口径） |
| `tests/fake_blender.py` | 新增 `--missing-scene`（模拟真机"找不到场景但退出码 0"） |

### 新增测试（+14，433 → 447）

- `TestSceneSelection::test_missing_scene_fails_instead_of_rendering_the_wrong_one`
- `TestSceneSelection::test_missing_scene_error_lists_the_real_scenes`
- `TestStopDoesNotEatRetry::test_cancel_during_a_frame_gives_the_attempt_back`
- `TestStopDoesNotEatRetry::test_crash_still_keeps_the_attempt`（防止把"崩溃必续"改坏）
- `TestAftermathPlan::test_cancelled_but_complete_still_triggers`（原 `test_cancelled_never_triggers` 按新口径重写）
- `TestDoneButCancelled` × 4（状态栏 / 真取消仍说已取消 / settle 清档 / 善后执行）
- `TestState::test_rollback_attempt_*` × 3（退额度 / 不退成负数 / 落盘往返）
- `TestFormValidation::test_triple_unlimited_warns` + 两条反向用例

### 回归重走（受影响 S-ID + 冒烟集）

| 链路 | 方式 | 结果 |
|---|---|---|
| S-22 场景名不存在 | 真 GUI + 假 Blender（`--missing-scene`） | ✅ `ok=False`、**0 帧产出**、存档保留且 `autoresume=False`、状态栏「未完成 0/2」、错误信息列出真实场景名 |
| S-30 停止不吃额度 | 真 GUI，停在帧 1 | ✅ `attempts={}`（额度已退回）、`done=[]`、状态栏「已取消 · 完成 0/3」 |
| S-01 / S-11 / S-16 / S-17 冒烟 | 真 GUI 跑 4 帧到完成 | ✅ `全部完成 4/4`、4 个产物、存档已删除、断点标签「已完成 4/4 帧」 |
| S-18 / S-31（P-W3） | 单测 | ✅ `TestDoneButCancelled` + `TestAftermathPlan` 全绿 |
| S-07（P-W4） | 单测 | ✅ 三不限给出警告、只选「一直重启」不误报 |
| 全量 | `unittest discover` | ✅ **447 条 OK（skipped=1）** |
| 边界 | `tools/check_boundaries.py` | ✅ 7/7 通过 |
| 静态 | `pyflakes` | ✅ exit 0 |

> **P-W4 的后续（v0.8.4）**：用户反馈重试那一块的控件"太细碎"，要求"一个参数控制所有的"。
> 三个参数（重启次数 / 单帧尝试 / 无进展兜底）合并成**一个下拉**：选「一直重复」则重启与
> 单帧都不限，选「N 次」则两者都按 N 次 —— 由 `guimodel.retry_plan(label)` 统一给出，
> 每个选项里兜底都是 3 轮，**"三重不限"这个组合从界面上拼不出来**，比给警告更彻底。
> 原警告与它的三条测试一并删除。全量测试从 447 条变为 436 条。
> （中途 v0.8.3 曾做成"一个勾选"两档，被用户打回：他要的是"一个次数"，不是"一个是非"。）

### 沉淀

- AGENTS 坑库：**56**（上游算出来但没人接的字段 = 死字段）、**57**（取消 ≠ 失败，记账要分开）、**58**（同一载荷被多处各自解释 → 界面说反话）；基线行更新到 v0.8.2
- CHANGELOG `[0.8.2]`：两个 🔴 + 三条口径统一 + 内部
- `brconsole/__init__.py` → `0.8.2`；README / DEVELOPMENT / AGENTS 测试数 433 → 447
- 素材修正：**0 处**（33 条素材全部对应真实入口，无 P-4 类错误）

## 迭代与停止（步骤 6）

**轮次小结（第 1 轮）**

| 四问 | 判断 |
|---|---|
| 🔴 未清零？ | 否 —— 2 个 🔴 全部修复并回归通过 |
| 未收敛？ | 是 —— 本轮新发现 6 个（上轮基线 0，无法按"≤ 上轮一半"判断），**还需第 2 轮确认** |
| 回归有红？ | 否 —— 447 全绿 + 7 条边界全过 + 3 条 GUI 端到端 |
| 探索有新？ | 是 —— 只开了 1 个 charter，「停止/继续」边界这条支线已单独开过（P-W2 来自它），但 P-W5 还没修 |

**结论：不满足停止条件**（收敛需连续 2 轮递减，目前只有 1 轮）。按上限 3 轮继续。

**残余风险（第 1 轮收尾）**

| 编号 | 风险分 | 状态 | 跟踪建议 |
|---|---|---|---|
| P-W5 断点标签不校验签名 | 3×2=6 | 未修（可选级） | 第 2 轮做：`state_summary` 接收 `signature`，不匹配时显示「断点：属于别的任务」。注意别在界面重写 `normalise_output_template` 规则（会与 `JobConfig.__init__` 漂开） |
| 真实 Blender 5.2.2 上的场景校验 | — | 只用假 Blender 验过 | 下次真机跑一轮 `-S 不存在的场景`，确认 driver 的回读确实拿到 `scene_ok=False`（本轮依赖 driver 既有实现 + 其 5.2.2 实测注释） |
| 注册表自启的真实拉起 | — | 沙箱内无法验证 | 真机重启一次确认 `--autostart` 能被拉起 |
| 下一轮 charter 建议 | — | 待开 | `Explore 关机/睡眠 + 通知 with 真实电源策略，to discover 倒计时窗口内的操作残留` |

