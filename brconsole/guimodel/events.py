# -*- coding: utf-8 -*-
"""core 事件的事实文案、任务结算与善后规则。

这一组的唯一变更原因是"一次 ran 结束后，界面/终端/存档该怎么说、怎么做"。
终端适配器与界面适配器只加符号和排版，不改事实句。
"""

from ..eta import fmt_duration
from .constants import (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN, AFTERMATH_SLEEP)


def outcome_reason(kind, ev):
    """任务结束事件 → `(类别, 原因文案)`，类别：`ok/cancelled/failed/incomplete`。

    `settle_task_action`、终端收尾、界面收尾都从这里取原因 —— 以前"你停止了任务"
    和"上次运行没能跑完"在三个文件里各写一遍，改一处就会让日志和存档原因对不上。
    """
    if kind == "job_done" and ev.get("ok"):
        return "ok", ""
    if ev.get("cancelled"):
        return "cancelled", "你停止了任务"
    if kind == "job_error":
        return "failed", "上次运行没能跑完（%s）" % (ev.get("error") or "异常")
    return "incomplete", "上次运行没能跑完（%s）" % (
        ev.get("error") or "有帧一直失败")


def event_text(kind, ev):
    """core 事件的**唯一事实文案**（不带符号/缩进），终端与界面共用。

    只收"事实句"：符号、缩进、进度行、多段提示由各自的适配器加。
    返回 None = 这一类没有共用文案，适配器自己处理。
    """
    if kind == "frame_error":
        return "帧 %s 渲染失败：%s" % (ev.get("frame"), ev.get("err"))
    if kind == "crash":
        return ("Blender 退出码 %s（第 %s 次重启），已完成 %s/%s，还剩 %s 帧"
                % (ev.get("rc"), ev.get("restart_index"), ev.get("done"),
                   ev.get("total"), len(ev.get("remaining") or [])))
    if kind == "warn":
        return str(ev.get("msg", ""))
    if kind == "resume":
        return "断点续跑：已完成 %s/%s 帧" % (len(ev.get("done") or []), ev.get("total"))
    if kind == "restart":
        return "第 %s 次重启，剩余帧 %s" % (ev.get("restart_index"), ev.get("pending"))
    if kind == "settings":
        return ("实际设置：引擎 %s | %sx%s @%s%% | 采样 %s | 输出 %s"
                % (ev.get("engine"), (ev.get("res") or [0, 0])[0],
                   (ev.get("res") or [0, 0])[1], ev.get("pct"),
                   "-" if ev.get("samples") is None else ev.get("samples"),
                   ev.get("output")))
    if kind == "give_up":
        return "重启次数已达上限，停止。剩余帧 %s" % ev.get("remaining")
    if kind == "no_progress":
        return ("连续 %s 轮一帧都没推进（已完成 %s/%s），停止 —— 重试解决不了的问题，"
                "检查工程或显存（剩余 %s）"
                % (ev.get("rounds"), ev.get("done"), ev.get("total"),
                   ev.get("remaining")))
    if kind == "device_set":
        return "渲染设备：%s（%s）" % (ev.get("type"), ", ".join(ev.get("devices") or []))
    if kind == "depth_set":
        return "输出位数：%s 位（%s）" % (ev.get("depth"), ev.get("file_format"))
    if kind == "collections_hidden":
        applied = ev.get("applied") or []
        missing = ev.get("missing") or []
        msg = "已隐藏集合：%s" % ("、".join(applied) if applied else "（无）")
        if missing:
            msg += " · 工程里找不到已忽略：%s" % "、".join(missing)
        return msg
    if kind == "engine_set":
        if ev.get("requested") == ev.get("actual"):
            return None
        return "引擎 %s → 实际 %s" % (ev.get("requested"), ev.get("actual"))
    if kind == "engine_info":
        return "实际场景：%s · 引擎 %s（%s 帧）" % (
            ev.get("scene") or "（工程默认）", ev.get("engine"),
            len(ev.get("frames") or []))
    if kind == "job_error":
        return str(ev.get("error"))
    if kind == "job_done":
        return ("任务结束：完成 %d/%d 帧 | 失败 %d | 放弃 %d | 重启 %d 次 | 用时 %s"
                % (len(ev.get("done") or []), ev.get("total", 0),
                   len(ev.get("failed") or {}), len(ev.get("exhausted") or []),
                   ev.get("restarts", 0), fmt_duration(ev.get("elapsed"))))
    return None


def settle_task_action(kind, ev):
    """一轮跑完后该怎么处置待办存档 → `(action, reason)`。

    `action`：`"clear"` = 删档；`"stop"` = 撤掉自动续跑标记（断点保留）。

    判据是「**进程有没有活着把结果报出来**」，而不是「成功还是失败」：

    - 报出「渲完了」→ 删档，否则下次开机会白跑一轮（虽然会立刻发现已完成，仍是噪音）
    - 报出「被取消 / 明确失败 / 抛异常」→ 撤掉自动续跑标记，**断点原样保留**。
      这些结果都是**进程活着**时才写得出来的；反过来，被强杀、掉电时根本来不及写文件，
      标记就还是 True → 下次开机自动续跑。这正是本功能要的那条路。
    - 换句话说：**崩溃能续跑，失败要能停下来**。不给明确失败保留自动续跑，是因为那会让
      一个注定失败的任务每次开机都白跑一次（最典型：场景里没有相机），变成开机自启的头号
      噪音；而且"关机导致的失败"根本不会走到这里 —— 那时进程是被直接杀掉的。

    ⚠️ 这段逻辑特意放在 guimodel（不碰 tkinter）——它决定"开机要不要自动跑"，
    是这套机制里最不能出错的一环，必须能被单测直接钉住。
    """
    category, reason = outcome_reason(kind, ev)
    if category == "ok":
        return "clear", ""
    return "stop", reason


# ---------------- 任务结束后的善后（关机 / 睡眠） ----------------
def aftermath_plan(choice, ok, cancelled):
    """任务结束后要不要执行善后动作 → 返回 `"shutdown"` / `"sleep"` / `None`。

    **只有"全部渲完"才触发**，这一点由 `ok` 单独决定：

    - 失败/放弃 = 正需要人来看原因，把机器睡了或关了反而把问题藏到明天
    - `cancelled` **不再单独一票否决**：最后一帧渲完的同一瞬间点停止时
      `ok` 和 `cancelled` 会同时为真（`core` 里两个值分别算）。此时用户要的
      「渲完关机」已经成立，硬按 cancelled 拦掉，他会看到"图全渲完了、机器却开着"。
      弹窗本身还带 60 秒可取消的倒计时，人在场也不会被惊到。
    - 真取消（还有帧没渲）时 `ok` 本来就是 False，自然不触发 —— 无需再判 cancelled。

    竞品（BRQ / Render Manager）的自动关机同样只在"队列 100% 完成"时触发，
    并且都带取消倒计时 —— 执行侧的倒计时见 `power`（60 秒，`shutdown /a` 可撤）。
    界面选项存的就是这里的值（`AFTERMATH_*`），别再各写一份字面量。
    """
    if choice not in (AFTERMATH_SHUTDOWN, AFTERMATH_SLEEP):
        return None
    if not ok:
        return None
    return choice


def event_line(kind, ev):
    """把 core 事件翻成一行面板日志；返回 None 表示这条不用显示。

    事实文案统一在 `event_text()`；这里只加符号前缀和界面特有的几种
    （逐帧进度、首帧提示、启动摘要）。
    """
    if kind == "native":
        return None                                  # 原生行由界面按开关单独处理
    if kind == "frame_done":
        tail = ""
        if ev.get("eta_sec") is not None:
            tail = "，剩余 %d 帧，ETA %s" % (ev.get("remaining") or 0,
                                             fmt_duration(ev.get("eta_sec")))
        return "✓ 帧 %s 完成 %s（%s/%s%s）%s" % (
            ev.get("frame"), fmt_duration(ev.get("secs")),
            ev.get("done"), ev.get("total"),
            " · 预热帧不计入基线" if ev.get("warmup") else "", tail)
    if kind == "stall":
        # 首帧前 Blender 要先读 .blend、加载全部贴图、把场景搬上 GPU ——
        # 大工程这一步几分钟没有输出都正常。旧文案只说"未知阶段"，
        # 用户最容易在这里误判卡死而点停止（实测量级：110s 加载 + 0.4s 渲染）。
        head = ("首帧准备中" if ev.get("first_frame") else "渲染中")
        return ("… %s已 %ss 没有新输出（%s）" % (head, ev.get("idle_sec"), ev.get("phase"))
                + ("：大工程加载贴图/上传场景可能要 1～3 分钟，请耐心等待，不要停止"
                   if ev.get("first_frame") else "：若阶段持续不变再检查工程/显存"))
    if kind == "run_start":
        return "启动 Blender（第 %s 轮，待渲染 %s 帧）" % (
            ev.get("restart_index"), len(ev.get("pending") or []))
    if kind == "job_start":
        scene = ev.get("scene")
        return "开始：%s（共 %s 帧%s）→ %s" % (
            ev.get("blend"), ev.get("total"),
            " · 场景 %s" % scene if scene else " · 用工程默认场景",
            ev.get("output"))
    text = event_text(kind, ev)
    if text is None:
        return None
    symbol = {"frame_error": "✗ ", "crash": "⚠ ", "restart": "↻ ",
              "give_up": "✗ ", "no_progress": "✗ "}.get(kind, "")
    return symbol + text
