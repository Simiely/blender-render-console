# -*- coding: utf-8 -*-
"""GUI 的**纯逻辑层**（不 import tkinter，便于单测，也便于换成别的界面库）。

分三块：
- `FormModel`  —— 表单字段 → `JobConfig`，含所有校验与"补全"规则
- `ProgressModel` —— 把 core 的事件流折算成界面要显示的几个字段
- `LogModel`   —— 日志面板的环形缓冲（防止长任务把内存吃满）

界面层（`gui.py`）只负责把这三个模型贴到控件上，业务逻辑一行都不写。
"""

import os
import re
import time
from collections import deque

from .core import (UNLIMITED, complete_output_template, expand_segment,
                   parse_restart_limit, state_file_for)
from .eta import fmt_clock, fmt_duration
from .state import JobState

KEEP = ""          # 下拉框里"保持工程设置"对应的实际值就是空

# 场景下拉的两个占位文案（都不是真实场景名，交给 scene_to_config 过滤掉）
SCENE_DEFAULT_LABEL = "（用工程默认场景）"
SCENE_NEED_READ_LABEL = "（先读取工程配置）"
SCENE_PLACEHOLDERS = (SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL)

# 「最多重启几次」下拉的选项。
#
# ⚠️ 这里**只放显示文案**，不再维护「文案 ↔ 实际值」两张表：早期版本的界面
# 用的是 `RESTART_VALUES`（裸的 `1/3/5/10/unlimited`），于是下拉里直接显示出了
# 英文 `unlimited`，而旁边那套中文文案（`RESTART_CHOICES`）从来没被任何控件用过。
# 现在文案本身就是要解析的输入，翻译交给 `core.parse_restart_limit`（它认中文）。
#
# ⚠️ 默认值是**「一直重启」**（v0.8.1 起）：崩溃续跑本来就是这个程序的卖点，
#    而有限次数（旧的默认 5 次）会在长任务里过早放弃 —— 剩下任意一帧没渲完，
#    整轮就得靠人回来点一次。次数仍然可以手动改成 1/3/5/10。
RESTART_OPTIONS = ["1 次", "3 次", "5 次", "10 次", "一直重启，直到全部渲完（默认）"]
DEFAULT_RESTART_OPTION = RESTART_OPTIONS[-1]
UNLIMITED_RESTART_LABEL = RESTART_OPTIONS[-1]

# 「限次类」两个字段的默认值。`FormModel` 与界面共用这一份，
# 避免"3"散落在好几处、改一处忘一处。
DEFAULT_ATTEMPTS = "3"
DEFAULT_NO_PROGRESS = "3"

# 「不限」的写法。0 是 core/state 里**既有**的约定：`JobState.remaining/exhausted` 用
# `max_attempts <= 0` 表示不限，`RenderJob` 用 `max_no_progress_rounds > 0` 才启用兜底。
# 所以"不限"根本不需要给内核加新语义，填 0 即可。
#
# ⚠️ **只把「单帧最多尝试」放成 0，无进展轮数保持 3**（v0.8.1 起）。
#    早先是 ("0", "0")，等于选中"一直重启"就顺手把**内核里唯一防死循环的闸门**关掉
#    （AGENTS 坑 39）：卡在同一处反复崩、一帧都渲不出来时，没有任何条件能让它停下来。
#    现在这两件事分开：重启次数不限（用户要的），但连续 3 轮毫无进展就停（兜底仍在）。
UNLIMITED_LIMITS = ("0", DEFAULT_NO_PROGRESS)

#: 第一段帧范围的默认起止（也是"没读到工程配置"时的兜底），界面与 FormModel 共用一份
DEFAULT_SEGMENT_START = "1"
DEFAULT_SEGMENT_END = "250"


def is_unlimited_restart(value):
    """这个重启选项是不是「一直重启」。

    **必须走 `parse_restart_limit` 而不是比对文案字符串**：下拉里存的是显示文案，
    重复一份字面量就等于把"文案"和"解析规则"绑死两处（历史上正是这么漂过一次 ——
    界面显示英文 `unlimited`，而中文文案表从来没有任何控件用过）。
    解析不了的值走 `default=None`，与「一直重启」区分开。
    """
    return parse_restart_limit(value, default=None) == UNLIMITED


def unlimited_limit_fill(restart_value):
    """选中某个重启选项时，「单帧最多尝试 / 连续无进展轮数」该**默认填**什么。

    选「一直重启，直到全部渲完」→ 返回 `("0", "3")`：
    - 单帧尝试填 `0`（=不限，内核既有约定）—— 否则帧会被判 exhausted 踢出队列，
      重试额度比"重启次数不限"先用完，那个选项就形同虚设；
    - 无进展轮数**保持 3** —— 它是内核里唯一防死循环的闸门。旧版把它也填 0，
      等于选完"一直重启"就再也没有任何终止条件了（见 AGENTS 坑 39）。

    **只改默认值、不锁控件**：留一条退路 —— 万一卡在同一帧反复崩，
    把无进展轮数改小就是更早刹车；改大/填 0 则是放开。
    不是这个选项就返回 None = 别动用户填的值。
    """
    return UNLIMITED_LIMITS if is_unlimited_restart(restart_value) else None


def restart_limits_plan(restart_value, attempts, no_progress, backup):
    """换重启选项时，两个限次框该显示什么 → `(attempts, no_progress, backup)`。

    界面上那三个值是紧耦合的：`max_frame_attempts=0`（=不限）**只该在「一直重启」底下出现**
    （否则就得到"重启次数有限的 5 次 + 单帧不限次数"这种组合 —— 一帧反复抛异常时
    单帧额度永远耗不尽，而重启次数又在数）。`no_progress` 则任何模式下都保持一个正数兜底。

    所以这里是一条明确的状态机：

    - 切到「一直重启」→ 填 `("0", "3")`，并把**离开时的还原值**放进 `backup`。
      已经在「一直重启」态里再点一次时**不覆盖**已有的 backup（否则备份会变成 0/3，
      回头"还原"出一对默认值 —— 等于没备份）。
    - 切回有限次数 → 有 backup 就还原它；没有 backup 就**原样不动**。
      "没有 backup" = 用户本来就在有限次数的选项之间切换（`1 次` → `5 次`），
      这时绝不能拿默认值去覆盖他填的 9/7。

    ⚠️ 从存档回填那条路不会留下 backup（回填是"还原存档"，不是"切换选项"），
    所以它必须**预先塞一份**进来 —— 见 `restore_limits_backup()`。
    少了这一步，切回有限次数时两个框会停在 0（=不限），实测踩过。
    """
    fill = unlimited_limit_fill(restart_value)
    if fill is not None:
        return fill[0], fill[1], (backup if backup is not None
                                  else (attempts, no_progress))
    if backup is None:
        return attempts, no_progress, None
    return backup[0], backup[1], None


def restore_limits_backup(restart_value, attempts, no_progress):
    """从存档回填表单后，给两个限次框**预置**一份还原值。

    只在存档处于「一直重启」时预置 —— 那种情况下两个框里的 `0` 是**这个选项的产物**
    （`unlimited_limit_fill` 的默认填充），不该跟着用户离开这个选项：
    他切到"5 次"要的是**有界**的重试。非 0 的值（用户在有限次数模式下特意填过的，
    比如 9）原样保留 —— 那是有意为之的偏好，不该丢。

    为什么不"把存档原值直接当备份"：那样 `0/0` 会被当成本事原样还原，
    绕一圈又回到"有限次数 + 两个不限"。（`0` 是"不限"还是"用户特意填的不限"，
    从存档里分辨不出来 —— 分不出来时按**有界**那一侧兜，这是更安全的方向。）
    """
    if not is_unlimited_restart(restart_value):
        return None
    pick = lambda v, d: d if str(v).strip() in ("", "0") else str(v)   # noqa: E731
    return (pick(attempts, DEFAULT_ATTEMPTS), pick(no_progress, DEFAULT_NO_PROGRESS))

ENGINES = [("保持工程设置（用 .blend 里的）", KEEP), ("Cycles", "CYCLES"),
           ("EEVEE", "BLENDER_EEVEE"), ("Workbench", "BLENDER_WORKBENCH")]
DEVICES = [("保持工程设置", KEEP), ("CPU", "CPU"), ("CUDA", "CUDA"),
           ("OptiX", "OPTIX"), ("HIP", "HIP"), ("oneAPI", "ONEAPI")]
FORMATS = [("保持工程设置", KEEP), ("PNG", "PNG"), ("JPEG", "JPEG"),
           ("OpenEXR", "OPEN_EXR"), ("TIFF", "TIFF")]


def scene_to_config(label):
    """场景下拉的显示值 → `JobConfig.scene`。

    占位文案和空值都表示"不指定，用工程里激活的那个场景"。
    """
    s = str(label or "").strip()
    if not s or s in SCENE_PLACEHOLDERS:
        return None
    return s


def fields_from_detail(detail, blend=""):
    """一个场景的配置详情 → 表单字段 dict（界面只负责把它 set 到控件上）。

    `detail` 来自 `inspect.read_blend_info()` 的 `scene_details[i]`（或顶层那份）。
    拿不准的字段**不放进结果** —— 宁可不改，也不要写一个错值进去。典型是
    Cycles 的 GPU 后端名：它来自用户偏好设置，`-b` 下常常读不到。
    """
    from .inspect import output_template_from

    if not detail:
        return {}
    out = {}
    if detail.get("frame_start") is not None:
        out["start"] = str(detail["frame_start"])
    if detail.get("frame_end") is not None:
        out["end"] = str(detail["frame_end"])
    if detail.get("frame_step"):
        out["step"] = str(detail["frame_step"])
    engine = (detail.get("engine") or "").upper()
    if engine in [v for _, v in ENGINES]:
        out["engine"] = engine
    if detail.get("samples") is not None:
        out["samples"] = str(detail["samples"])
    # 设备：只有能确定才改（GPU 后端名来自偏好设置）
    if (detail.get("cycles_device") or "").upper() == "CPU":
        out["device"] = "CPU"
    else:
        cdt = (detail.get("compute_device_type") or "").upper()
        if cdt in [v for _, v in DEVICES]:
            out["device"] = cdt
    res = detail.get("resolution") or []
    if len(res) == 2:
        out["width"] = str(res[0])
        out["height"] = str(res[1])
    if detail.get("resolution_percentage"):
        out["pct"] = str(detail["resolution_percentage"])
    fmt = (detail.get("file_format") or "").upper()
    if fmt in [v for _, v in FORMATS]:
        out["file_format"] = fmt
    tpl = output_template_from(detail.get("output_path"), blend)
    if tpl:
        out["output"] = tpl
    return out


def frames_to_text(frames):
    """帧号列表 → `1-240,300` 这样的紧凑文本。

    只把**连续 3 帧以上**的折成区间：`1-240` 比列 240 个数字清楚得多。
    （老版本界面直接把这个文本填进输入框；现在界面用帧段列表，它保留了
    `parse_frames` 的往返测试作为对账用。）

    ⚠️ 折成区间后会受全局 `step` 影响（`parse_frames` 对区间是 `range(a, b+1, step)`），
    所以拿它还原时必须把 step 一起置回 1。
    """
    fs = sorted({int(f) for f in (frames or [])})
    if not fs:
        return ""
    out, i = [], 0
    while i < len(fs):
        j = i
        while j + 1 < len(fs) and fs[j + 1] == fs[j] + 1:
            j += 1
        if j - i >= 2:
            out.append("%d-%d" % (fs[i], fs[j]))
        else:
            out.extend(str(f) for f in fs[i:j + 1])
        i = j + 1
    return ",".join(out)


# ---------------- 帧段（界面上「添加一段 → 填起/止/步长」的那个列表）----------------
#
# 设计说明（为什么是"段"，不再是"起/止/步长 + 一个帧列表输入框"）：
# 早先界面只有一组 起/止/步长，多段不连续帧要靠最右边那个约 90px 宽、
# 没写语法、报错还是 Python 原文的「帧列表」输入框，实际不可用。
# 现在改成**段列表**：一段一行、可增可删、每段自带步长，全部经
# `core.expand_segment` 展开 —— 区间展开规则全项目只此一处。

def new_segment(start="", end="", step=""):
    """一个空帧段。**界面与存档回填共用**，别在各处手写这个 dict 字面量。"""
    return {"start": str(start), "end": str(end), "step": str(step)}


def normalise_segment(seg):
    """把外来 dict（可能缺键 / 值不是字符串）归一成 `{start,end,step}` 三个字符串。"""
    seg = seg or {}
    return {k: ("" if seg.get(k) is None else str(seg.get(k)).strip())
            for k in ("start", "end", "step")}


def segment_label(seg, step=1):
    """一段 → 人类可读描述（实时回显用）：`1-250` / `1-200 每 5 帧` / `第 250 帧`。"""
    seg = normalise_segment(seg)
    s, e, st = seg["start"], seg["end"], seg["step"]
    if not s and not e:
        return "（未填）"
    if not s or not e:
        # 只填了一边 = 单帧：回显必须写成「第 N 帧」，不能让人以为它是"从 N 到工程末帧"
        return "第 %s 帧" % (s or e)
    try:
        a, b = int(s), int(e)
    except ValueError:
        return "%s-%s" % (s, e)
    if b < a:
        a, b = b, a
    if a == b:
        return "第 %d 帧" % a
    eff = st or str(step or 1)
    return "%d-%d%s" % (a, b, "" if eff == "1" else " 每 %s 帧" % eff)


def segments_to_frames(segments, step=1):
    """界面上的帧段列表 → `(帧号列表, 错误列表)`。

    段语义（每一条都要能被界面上的回显看出来，别让它变成"填了才知道"）：
    - 起/止 **都空** → 这一段还没填，跳过（不算错 —— 刚点「添加一段」的空行就是这状态）
    - 只填一边 → **单帧**（回显写成「第 N 帧」）
    - 都填 → 区间，起 > 止 自动交换（填反了而已）
    - 步长空 → 用全局 `step`；步长 < 1 报错（静默当 1 会让"每 5 帧"变成每帧都渲）
    - 段之间重复的帧去重后升序 —— 两段重叠不会被渲两遍

    错误一律带**段号**：界面上有若干行，不说是哪一行等于没说。
    """
    frames, errors = [], []

    def as_int(text, label, no):
        t = str(text or "").strip()
        if not t:
            return None, False
        try:
            return int(t), False
        except ValueError:
            errors.append("第 %d 段：%s 得是整数，当前填的是「%s」" % (no, label, t))
            return None, True

    for i, raw in enumerate(segments or [], 1):
        seg = normalise_segment(raw)
        if not seg["start"] and not seg["end"]:
            continue
        s, bad_s = as_int(seg["start"], "起帧", i)
        e, bad_e = as_int(seg["end"], "止帧", i)
        st, bad_st = as_int(seg["step"], "步长", i)
        if bad_s or bad_e or bad_st:
            continue
        try:
            if s is None or e is None:
                frames.append(e if s is None else s)
            else:
                frames.extend(expand_segment(s, e, st if st is not None else step))
        except ValueError as exc:
            errors.append("第 %d 段：%s" % (i, exc))

    seen, out = set(), []
    for f in frames:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return sorted(out), errors


def frames_to_segments(frames, step=1):
    """帧号列表 → 帧段列表（开机续跑回填界面用）。

    ⚠️ 存档里只有**展开后的帧号**（`JobConfig` 不存段、只存 frames），所以"每 5 帧"
    这件事回填时是**推断**出来的：按相邻差值恒定切段 —— 差值一变就新起一段。
    `[1..240]` → 一段每 1 帧；`[1,6,11,26]` → `1-11 每 5 帧` + `26`。
    推断只影响"界面显示成几段"，展开回去的帧号与原列表逐项相等（有往返单测钉着）。
    """
    fs = sorted({int(f) for f in (frames or [])})
    if not fs:
        return []
    segs, i = [], 0
    while i < len(fs):
        if i + 1 >= len(fs):                      # 落单的一帧
            segs.append(new_segment(fs[i], "", ""))
            break
        d = fs[i + 1] - fs[i]
        j = i + 1
        while j + 1 < len(fs) and fs[j + 1] - fs[j] == d:
            j += 1
        segs.append(new_segment(fs[i], fs[j], d))
        i = j + 1
    return segs


#: 回显里最多列几段就省略（多了会撑破那一行）
PREVIEW_MAX_SEGMENTS = 4
#: 合并后的紧凑写法最多留多少个字符（`1-2500,3000-3200` 这种）
PREVIEW_MAX_MERGED = 60
#: 单段且帧数不超过这个数时，回显里把帧号全列出来
PREVIEW_MAX_LIST = 12


def frames_preview(segments, step=1):
    """帧段列表 → `(帧号列表, 回显文本, 是否合法)`。

    界面每改一个字符就调它一次：合法 → 绿字「共 N 帧：…」，
    不合法 → 红字「！第 2 段：…」。**按下「开始渲染」前就能看出问题在哪**，
    而不是等弹框。
    """
    frames, errors = segments_to_frames(segments, step)
    if errors:
        return frames, "！ " + "；".join(errors), False
    if not frames:
        return frames, "！ 还没有可渲染的帧 —— 至少填一段的起帧", False

    labels = [segment_label(sg, step) for sg in (segments or [])]
    labels = [x for x in labels if x != "（未填）"]
    if len(labels) > PREVIEW_MAX_SEGMENTS:
        labels = labels[:PREVIEW_MAX_SEGMENTS] + ["…"]
    body = " · ".join(labels)
    if len(labels) > 1:
        # 多段时再补一句"合并后是什么"：两个段**重叠或首尾相接**时，光看每段看不出
        # 最后究竟渲哪些帧（重复的帧会被去掉、只渲一次）。这一句就是那份对账。
        merged = frames_to_text(frames)
        if len(merged) <= PREVIEW_MAX_MERGED:
            body += "；合并后 %s" % merged
    elif len(frames) <= PREVIEW_MAX_LIST:
        body += "（%s）" % ", ".join(str(f) for f in frames)
    return frames, "共 %d 帧：%s" % (len(frames), body), True



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
    if kind == "job_done" and ev.get("ok"):
        return "clear", ""
    reason = ("你停止了任务" if ev.get("cancelled")
              else "上次运行没能跑完（%s）" % (ev.get("error") or "有帧一直失败"))
    return "stop", reason


# ---------------- 任务结束后的善后（关机 / 睡眠） ----------------
AFTERMATH_NOTHING = "nothing"
AFTERMATH_SHUTDOWN = "shutdown"
AFTERMATH_SLEEP = "sleep"
AFTERMATH_VALUES = (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN, AFTERMATH_SLEEP)


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


def restart_label_for(value):
    """重启次数（存档里的整数）→ 下拉文案。

    `-1` / None（= 一直重启）映射到最后一个选项；不在已知选项里的整数
    直接写成 `"7 次"` —— `parse_restart_limit` 认得出开头数字，
    界面那边也会把这个文案补进下拉的候选里（否则 readonly 的下拉放不下它）。
    """
    if value is None:
        return RESTART_OPTIONS[-1]
    n = int(value)
    if n < 0:
        return RESTART_OPTIONS[-1]
    for label in RESTART_OPTIONS[:-1]:
        if parse_restart_limit(label, default=None) == n:
            return label
    return "%d 次" % n


class FormModel(object):
    """界面表单的字段容器。`to_config()` 负责校验与补全。

    ⚠️ 帧范围是 **`segments`（段列表）**，不再是 `start/end/step` 三个单值：
    多段不连续的序列帧（`1-100 每 5 帧` + `250` + `300-400`）是常规用法，
    单值表示法根本表达不了。三个单值属性已删除 —— 留着就会有控件去读它，
    然后"界面上明明改了段、跑的还是旧值"。
    """

    def __init__(self, **kw):
        self.blend = kw.get("blend", "")
        self.blender = kw.get("blender", "")
        self.output = kw.get("output", "")
        #: 帧段列表：[{"start","end","step"}, ...]，值都是字符串（界面控件里就是字符串）
        segs = kw.get("segments")
        self.segments = ([normalise_segment(s) for s in segs] if segs
                         else [new_segment(DEFAULT_SEGMENT_START, DEFAULT_SEGMENT_END)])
        self.engine = kw.get("engine", KEEP)
        self.samples = kw.get("samples", "")
        self.device = kw.get("device", KEEP)
        self.width = kw.get("width", "")
        self.height = kw.get("height", "")
        self.pct = kw.get("pct", "")
        self.file_format = kw.get("file_format", KEEP)
        self.scene = kw.get("scene", SCENE_DEFAULT_LABEL)   # 场景名，或占位文案
        self.resume = kw.get("resume", True)
        self.max_restarts = kw.get("max_restarts", DEFAULT_RESTART_OPTION)
        self.max_no_progress = kw.get("max_no_progress", DEFAULT_NO_PROGRESS)
        self.max_frame_attempts = kw.get("max_frame_attempts", DEFAULT_ATTEMPTS)
        # ⚠️ 「显示原生输出」曾经在这里有个 `show_native` 字段：界面把它存进来，
        #    但**全项目没有一处读它**（真正的开关是 gui 里的 `v_native`）。
        #    这正是 AGENTS 坑 35「开关不通电」的同款 —— 已删除，别再把它加回来。
        # 界面上没有这两个控件（CLI 专有参数），但**必须原样透传**：
        # 从任务存档回填时如果丢掉它们，重建出来的任务会静默按默认值跑 ——
        # 「参数悄悄变了」比「报错」难查得多。
        self.extra_args = list(kw.get("extra_args") or ())
        self.restart_delay = kw.get("restart_delay", 2.0)

    # ---------- 补全 ----------
    @staticmethod
    def complete_output(path, blend=""):
        """用户给目录/文件名时自动补出 `name_####.ext` 形式的模板。

        规则本体在 `core.complete_output_template()` —— CLI 与 GUI 必须共用一份，
        别在这里重写（`-o render` 这种输入，两个入口曾给出两种落点）。
        """
        return complete_output_template(path, blend)

    @staticmethod
    def default_blend_dir(blend):
        return os.path.dirname(os.path.abspath(blend)) if blend else ""

    # ---------- 存档 → 表单（开机续跑时把上次的任务填回界面）----------
    @classmethod
    def from_config(cls, cfg_dict, blender_exe=""):
        """`JobConfig.to_dict()` 的结果 → 表单字段。

        帧范围走 **`frames_to_segments`**：存档里只有展开后的帧号列表
        （`JobConfig` 不存段），这里按"相邻差值恒定"把它还原成段 ——
        回填的界面上看到的段数、步长可能与当初填的不一样（`1,6,11,26` 会被
        还原成「1-11 每 5 帧」+「26」），但**展开回去的帧号逐项相同**，
        也就是"填回去和上次跑的是同一批帧"。这个往返有单测盯着。
        """
        d = dict(cfg_dict or {})
        res = d.get("resolution") or []
        return cls(
            blend=d.get("blend") or "",
            blender=blender_exe or "",
            output=d.get("output_template") or "",
            segments=frames_to_segments(d.get("frames")) or None,
            engine=d.get("engine") or KEEP,
            samples="" if d.get("samples") is None else str(d["samples"]),
            device=d.get("device") or KEEP,
            width=str(res[0]) if len(res) == 2 else "",
            height=str(res[1]) if len(res) == 2 else "",
            pct=("" if d.get("resolution_percentage") is None
                 else str(d["resolution_percentage"])),
            file_format=d.get("file_format") or KEEP,
            scene=d.get("scene") or SCENE_DEFAULT_LABEL,
            resume=bool(d.get("resume", True)),
            max_restarts=restart_label_for(d.get("max_restarts", 5)),
            max_no_progress=str(d.get("max_no_progress_rounds", int(DEFAULT_NO_PROGRESS))),
            max_frame_attempts=str(d.get("max_frame_attempts", int(DEFAULT_ATTEMPTS))),
            extra_args=d.get("extra_args") or (),
            restart_delay=d.get("restart_delay", 2.0),
        )

    # ---------- 校验 → JobConfig ----------
    def to_config(self):
        """返回 (JobConfig|None, errors:list[str], warnings:list[str])。

        帧号由 `segments_to_frames`（本模块，纯逻辑）展开，它内部走
        `core.expand_segment` —— 界面**不自己实现**区间展开规则，
        与命令行 `parse_frames` 共用同一个原语。
        """
        from .core import JobConfig

        errors, warns = [], []

        blend = (self.blend or "").strip()
        if not blend:
            errors.append("还没选 .blend 工程文件")
        elif not os.path.exists(blend):
            errors.append("工程文件不存在：%s" % blend)
        elif not blend.lower().endswith(".blend"):
            warns.append("这个文件的扩展名不是 .blend，确认没选错？")

        frames, frame_errors = segments_to_frames(self.segments)
        errors.extend(frame_errors)
        if not frames:
            errors.append("没有可渲染的帧 —— 在「帧范围」里至少填一段的起帧")

        output = self.complete_output(self.output.strip(), blend)
        if not output:
            errors.append("还没填输出模板")

        def opt_int(v, name):
            v = str(v).strip()
            if not v:
                return None
            try:
                return int(v)
            except ValueError:
                errors.append("%s 必须是整数，当前：%s" % (name, v))
                return None

        samples = opt_int(self.samples, "采样")
        pct = opt_int(self.pct, "分辨率百分比")
        width = opt_int(self.width, "宽度")
        height = opt_int(self.height, "高度")
        resolution = (width, height) if (width and height) else None
        if bool(width) != bool(height):
            errors.append("分辨率要填就宽高都填")

        attempts = opt_int(self.max_frame_attempts, "单帧最多尝试")
        if attempts is not None and attempts < 0:
            attempts = 0                       # 负数没有意义，按「不限」处理
            warns.append("单帧最多尝试填了负数，按「不限」处理")
        no_progress = opt_int(self.max_no_progress, "连续无进展上限")
        if no_progress is not None and no_progress < 0:
            errors.append("连续无进展上限不能是负数")

        if errors:
            return None, errors, warns

        eff_attempts = int(DEFAULT_ATTEMPTS) if attempts is None else attempts
        eff_no_progress = int(DEFAULT_NO_PROGRESS) if no_progress is None else no_progress
        max_restarts = parse_restart_limit(self.max_restarts, default=5)
        # 三个「不限」同时成立 = 内核里**没有任何终止条件**：崩在一帧上就无限重启，
        # 每 2 秒拉起一次 Blender，只能人肉点停止。界面把 0 宣传成"不限制"，
        # 用户照着填很容易凑齐这三个（P-W4）。不硬拦（他可能真要跑通为止），但必须说清。
        if max_restarts < 0 and eff_attempts <= 0 and eff_no_progress <= 0:
            warns.append("重启次数、单帧最多尝试、连续无进展**三个都设成了不限** —— "
                         "这次任务没有任何终止条件，渲不出来的帧会一直重启下去，"
                         "只能手动点「停止」。建议把「连续无进展」改回 3。")

        cfg = JobConfig(
            blend=blend, frames=frames, output_template=output,
            engine=self.engine or None, samples=samples,
            device=self.device or None, resolution=resolution,
            resolution_percentage=pct, file_format=self.file_format or None,
            scene=scene_to_config(self.scene),
            state_path=None, log_path=None,
            max_restarts=max_restarts,
            max_frame_attempts=eff_attempts,
            resume=bool(self.resume),
            extra_args=self.extra_args,
            restart_delay=self.restart_delay,
            max_no_progress_rounds=eff_no_progress)
        return cfg, errors, warns

    def as_dict(self):
        return dict(self.__dict__)


class ProgressModel(object):
    """把 core 的事件流折算成界面字段。**不碰任何控件**。"""

    def __init__(self):
        self.reset()

    def reset(self):
        """把模型恢复成"还没开始跑"。

        ⚠️ **同一个任务的第二次开始也必须调它**（界面的 `on_start` 会调）。
        原先 `job_start` 里手写了一份"重置清单"，只列了一部分字段，漏掉
        `cancelled / elapsed / current_frame / stalled / eta_sec / ok / last_message` ——
        于是「停止 → 再开始」之后：状态栏整轮都显示「**已取消** · 完成 3/6 · 用时 1.9s」
        （1.9s 还是上一轮的用时），而且 `status_text()` 里 `cancelled` 的判断在
        `running` 之前，一路盖住了"正在渲染帧 N"。
        现在默认值只有这一份，`job_start` 直接复用它，**加字段不会再漏**。
        """
        self.running = False
        self.finished = False
        self.total = 0
        self.done = 0
        self.failed = 0
        self.exhausted = 0
        self.current_frame = None
        self.per_frame = None
        self.eta_sec = None
        self.eta_mode = ""
        self.phase = ""
        self.restarts = 0
        self.crashes = 0
        self.elapsed = 0.0
        self.ok = False
        self.cancelled = False
        self.error = ""
        self.stalled = False
        self.last_message = ""
        #: 本任务是什么时候点开始跑的（job_start 收到时记）/ 是什么时候结束的
        #: （job_done 收到时记）—— 显示「预计 x 完成 / 已于 x 完成」用。
        #: 都是**本地墙钟**，不问 core 要（core 只报相对量 elapsed / eta_sec）。
        self.started_at = None
        self.finished_at = None

    def on_event(self, kind, ev):
        if kind == "job_start":
            self.reset()                 # 上一轮的残留一律清掉（见 reset 的说明）
            self.running = True
            self.started_at = time.time()
            self.total = ev.get("total") or 0
            # 续跑时 core 会带上**已完成帧数**：进度条要立刻落在真实位置，
            # 而不是先显 0% 再在第一帧完成时跳到 "2/6"（那样看着像断点没加载上）
            self.done = ev.get("done") or 0
            self.phase = ""
        elif kind == "frame_start":
            self.current_frame = ev.get("frame")
            self.stalled = False
        elif kind == "frame_done":
            self.current_frame = None
            self.done = ev.get("done") or self.done + 1
            self.total = ev.get("total") or self.total
            self.per_frame = ev.get("per_frame")
            self.eta_sec = ev.get("eta_sec")
            self.eta_mode = ev.get("eta_mode") or ""
            self.stalled = False
            if ev.get("warmup"):
                self.last_message = "首帧已完成（预热帧不计入 ETA 基线）"
        elif kind == "frame_error":
            self.failed += 1
            self.last_message = "帧 %s 失败：%s" % (ev.get("frame"), ev.get("err"))
        elif kind == "native":
            if ev.get("phase"):
                self.phase = ev["phase"]
                self.stalled = False
        elif kind == "stall":
            self.stalled = True
            self.phase = ev.get("phase") or self.phase
        elif kind == "crash":
            self.crashes += 1
            self.restarts = ev.get("restart_index") or self.restarts
            self.last_message = "Blender 退出码 %s，准备第 %s 次重启" % (
                ev.get("rc"), ev.get("restart_index"))
        elif kind == "restart":
            self.restarts = ev.get("restart_index") or self.restarts
        elif kind == "run_finished":
            self.last_message = "本轮子进程结束：成功 %s / 失败 %s" % (
                ev.get("ok"), ev.get("failed"))
        elif kind == "give_up":
            self.last_message = "重启次数已达上限，停止"
        elif kind == "no_progress":
            self.last_message = "连续 %s 轮没有任何帧推进，停止" % ev.get("rounds")
        elif kind == "warn":
            self.last_message = ev.get("msg", "")
        elif kind == "job_done":
            self.running = False
            self.finished = True
            self.finished_at = time.time()
            self.done = len(ev.get("done") or [])
            self.failed = len(ev.get("failed") or {})
            self.exhausted = len(ev.get("exhausted") or [])
            self.restarts = ev.get("restarts") or self.restarts
            self.elapsed = ev.get("elapsed") or 0.0
            self.ok = bool(ev.get("ok"))
            self.cancelled = bool(ev.get("cancelled"))
        elif kind == "job_error":
            self.running = False
            self.finished = True
            self.error = ev.get("error") or ""

    # ---------- 给控件用 ----------
    @property
    def fraction(self):
        """0~1；没有总数时返回 0（界面据此切 indeterminate）。"""
        if not self.total:
            return 0.0
        return max(0.0, min(1.0, self.done / float(self.total)))

    def status_text(self):
        if not self.running and not self.finished:
            return "就绪"
        # 「全部渲完」优先于「收到过取消」：最后一帧渲完的同一瞬间点停止时，
        # `cancelled` 和 `ok` 会同时为真。此时说「已取消 · 完成 6/6」是自相矛盾的
        # —— 存档那一侧已经按 ok 删了，界面不该反过来说没成。
        if self.cancelled and not (self.finished and self.ok):
            text = "已取消 · 完成 %d/%d · 用时 %s" % (
                self.done, self.total, fmt_duration(self.elapsed))
            if self.finished_at:
                text += " · 停止于 %s" % fmt_clock(self.finished_at, seconds=True)
            return text
        if self.finished:
            tail = "用时 %s" % fmt_duration(self.elapsed)
            if self.failed or self.exhausted:
                tail += " · 失败 %d · 放弃 %d" % (self.failed, self.exhausted)
            # 实际完成时刻 —— 与预估时间区分开，这才是"真的渲完"的时间点
            if self.finished_at:
                tail += " · 已于 %s 完成" % fmt_clock(self.finished_at, seconds=True)
            return ("全部完成 %d/%d · %s" if self.ok
                    else "未完成 %d/%d · %s") % (self.done, self.total, tail)
        if self.current_frame is not None:
            return "正在渲染帧 %s · 已完成 %d/%d%s" % (
                self.current_frame, self.done, self.total, self._elapsed_suffix())
        if self.stalled:
            return "%s（已有一会儿没有新输出，属于正常现象）%s" % (
                self.phase or "正在渲染", self._elapsed_suffix())
        if self.phase:
            return "%s%s" % (self.phase, self._elapsed_suffix())
        return "已完成 %d/%d%s" % (self.done, self.total, self._elapsed_suffix())

    def _elapsed_suffix(self):
        """运行中那几行尾巴上的「已用 X」—— 与 ETA（还剩多久）是一对：
        一个回答"跑了多久了"，一个回答"还要多久"。
        """
        if self.started_at is None:
            return ""
        return " · 已用 %s" % fmt_duration(time.time() - self.started_at)

    def eta_text(self):
        if self.finished or not self.running:
            return ""
        if self.eta_sec is None:
            return "ETA 计算中…"
        # ETA 是"还剩多久"；下面这句把它换算成"预计几点几分完成" ——
        # 过夜挂机时，人真正想看的往往就是这个（今天只显 HH:MM，跨天带日期，见 fmt_clock）
        finish = time.time() + self.eta_sec
        return "ETA %s（%s）· 预计 %s 完成" % (
            fmt_duration(self.eta_sec), self.eta_mode or "-", fmt_clock(finish))

    def per_frame_text(self):
        return ("单帧 %s" % fmt_duration(self.per_frame)) if self.per_frame else ""

    def summary(self):
        return {"total": self.total, "done": self.done, "failed": self.failed,
                "exhausted": self.exhausted, "restarts": self.restarts,
                "crashes": self.crashes, "elapsed": self.elapsed, "ok": self.ok}


class NativeLineFilter(object):
    """Blender 原生输出的**折叠器**（日志面板用，纯逻辑可单测）。

    为什么需要它：`-b` 下 Blender 的采样进度是用 `\\r` 原地刷新的，`core._pump` 会把它切成
    一行行推过来 —— 一帧 64 采样就是几十行，一个四小时的任务能吐几万行。
    原生输出默认开启之后，这些行会瞬间刷爆日志面板，更要紧的是**把真正的 traceback 冲走**。

    三条规则（都钉在单测里）：

    1. **错误行永不折叠**：含 error / traceback / warning / can't find / failed 的行原样放行
       —— 它们才是排错时要看的东西；
    2. **同类进度行按「帧号」折叠**，且两次输出间隔不少于 `interval` 秒；
       被折掉的行数累加，附在下一条输出的**前面**（`… 上一段进度已折叠 N 行`）；
    3. **换类时先把账交代清楚**：不交代的话，"折叠了多少行"就永远看不见了。

    只折叠采样进度（`Sample 12/64` / `Rendering 12 / 64 samples`）。别的原生行
    （阶段、Saved、着色器编译）本来就不重复，逐行显示才有信息量。
    """

    IMPORTANT = re.compile(
        r"error|traceback|warning|exception|failed|can't find|cannot|not found|"
        r"no such file|invalid", re.I)
    SAMPLE = re.compile(r"Sample\s+\d+\s*/\s*\d+|Rendering\s+\d+\s*/\s*\d+\s+samples",
                        re.I)
    FRAME = re.compile(r"Fra:\s*(-?\d+)")

    def __init__(self, interval=1.0, clock=None):
        self.interval = interval
        # 注入时钟是为了能单测：不真的 sleep，直接把时间"往前拨"
        self._clock = clock or time.time
        self._key = None
        self._last_emit = 0.0
        self._suppressed = 0

    def reset(self):
        """新任务开始时清账（否则上一轮漏下的折叠数会算到这一轮头上）。"""
        self._key = None
        self._last_emit = 0.0
        self._suppressed = 0

    def _collapse_key(self, line):
        """同类行的判据；返回 None = 这一行不参与折叠。"""
        if not self.SAMPLE.search(line):
            return None
        m = self.FRAME.search(line)
        return ("sample", m.group(1) if m else "?")

    def _take_summary(self, lines):
        """把待交代的折叠数拼到这批输出最前面。"""
        if not self._suppressed:
            return lines
        n, self._suppressed = self._suppressed, 0
        return ["… 上一段进度已折叠 %d 行（同类进度行只保留最新）" % n] + lines

    def feed(self, line):
        """消化一行原生输出 → 该显示的行（可能为空列表 = 被折掉了）。"""
        line = (line or "").rstrip()
        if not line:
            return []
        # ① 错误/警告：原样放行（即使和上一行一模一样也要放，重复的 traceback 也是证据）
        if self.IMPORTANT.search(line):
            return self._take_summary([line])
        key = self._collapse_key(line)
        # ② 非采样行：逐行放行
        if key is None:
            return self._take_summary([line])
        # ③ 采样行：同帧且间隔不够 → 折掉，只累计
        now = self._clock()
        if key == self._key and (now - self._last_emit) < self.interval:
            self._suppressed += 1
            return []
        out = self._take_summary([line])
        self._key, self._last_emit = key, now
        return out

    def flush(self):
        """任务结束时把最后一段的表达清楚（不然尾部折叠数就丢了）。"""
        return self._take_summary([])


class LogModel(object):
    """日志面板的**待消费队列**。

    长任务会吐几万行，所以模型只攒"还没被界面取走的行"（界面按帧率批量取）。
    行数上限有两道：
    - 这里 `maxlen` 兜住**内存**（界面来不及取时不能无限涨）；
    - 界面 Text 控件另有 `LOG_MAX_LINES` 兜住**渲染开销**。

    丢掉的行数记在 `dropped` 里、由界面明说一句 —— 静默丢日志等于静默丢证据。
    """

    def __init__(self, capacity=4000, maxlen=20000):
        self.capacity = capacity
        self.maxlen = maxlen
        self._pending = deque()
        self.total = 0                 # 累计收到的行数（界面对账用）
        self.dropped = 0               # 因为 maxlen 上限被丢掉的最早行数

    def add(self, text):
        for line in str(text).replace("\r", "\n").split("\n"):
            if line.strip():
                if self.maxlen and len(self._pending) >= self.maxlen:
                    self._pending.popleft()
                    self.dropped += 1
                self._pending.append(line.rstrip())
                self.total += 1

    def drain(self, limit=500):
        """取走待显示的行（最多 limit 条，避免一次卡死界面）。"""
        out = []
        while self._pending and len(out) < limit:
            out.append(self._pending.popleft())
        return out

    def clear(self):
        self._pending.clear()
        self.dropped = 0               # 清空日志 = 重新开始记账

    def __len__(self):
        return len(self._pending)


def event_line(kind, ev):
    """把 core 事件翻成一行面板日志；返回 None 表示这条不用显示。"""
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
    if kind == "frame_error":
        return "✗ 帧 %s 失败：%s" % (ev.get("frame"), ev.get("err"))
    if kind == "crash":
        return "⚠ Blender 退出（码 %s），已完成 %s/%s，准备第 %s 次重启" % (
            ev.get("rc"), ev.get("done"), ev.get("total"), ev.get("restart_index"))
    if kind == "restart":
        return "↻ 第 %s 次重启，剩余帧 %s" % (ev.get("restart_index"), ev.get("pending"))
    if kind == "run_start":
        return "启动 Blender（第 %s 轮，待渲染 %s 帧）" % (
            ev.get("restart_index"), len(ev.get("pending") or []))
    if kind == "stall":
        return "… 已 %ss 没有新输出（%s），大场景同步属于正常现象" % (
            ev.get("idle_sec"), ev.get("phase"))
    if kind == "warn":
        return "! %s" % ev.get("msg", "")
    if kind == "give_up":
        return "✗ 重启次数已达上限，停止（剩余 %s）" % ev.get("remaining")
    if kind == "no_progress":
        return ("✗ 连续 %s 轮一帧都没推进（已完成 %s/%s），停止 —— 重试解决不了的问题，"
                "看看是不是工程/显存本身有问题（剩余 %s）"
                % (ev.get("rounds"), ev.get("done"), ev.get("total"), ev.get("remaining")))
    if kind == "resume":
        return "断点续跑：已完成 %s/%s 帧" % (len(ev.get("done") or []), ev.get("total"))
    if kind == "settings":
        return "实际设置：引擎 %s · %sx%s@%s%% · 采样 %s" % (
            ev.get("engine"), (ev.get("res") or [0, 0])[0], (ev.get("res") or [0, 0])[1],
            ev.get("pct"), "-" if ev.get("samples") is None else ev.get("samples"))
    if kind == "device_set":
        return "渲染设备：%s（%s）" % (ev.get("type"), ", ".join(ev.get("devices") or []))
    if kind == "engine_set" and ev.get("requested") != ev.get("actual"):
        return "引擎 %s → 实际 %s" % (ev.get("requested"), ev.get("actual"))
    if kind == "job_done":
        return "任务结束：完成 %s/%s · 失败 %s · 放弃 %s · 重启 %s 次 · 用时 %s" % (
            len(ev.get("done") or []), ev.get("total"), len(ev.get("failed") or {}),
            len(ev.get("exhausted") or []), ev.get("restarts"),
            fmt_duration(ev.get("elapsed")))
    if kind == "job_error":
        return "✗ %s" % ev.get("error")
    if kind == "job_start":
        scene = ev.get("scene")
        return "开始：%s（共 %s 帧%s）→ %s" % (
            ev.get("blend"), ev.get("total"),
            " · 场景 %s" % scene if scene else " · 用工程默认场景",
            ev.get("output"))
    return None


def guess_state_path(output_template):
    """断点文件的默认位置。

    直接走 core 的 `state_file_for` —— **不要在这里再写一遍目录拼接规则**：
    界面拿它显示"断点：已完成 120 帧 / 清空断点"，运行时用它读写断点，
    两处规则一旦漂开，界面会说"已完成 120 帧"而实际从一开始渲（不报错，只是骗人）。
    """
    return state_file_for(output_template or "")


def state_summary(state_path):
    """给界面显示「断点里已完成多少帧」。文件不存在/损坏都返回 None。"""
    if not state_path or not os.path.exists(state_path):
        return None
    st = JobState.load(state_path)
    if st is None:
        return None
    return {"done": st.done_count, "total": st.total,
            "failed": len(st.failed), "frames": list(st.frames)[:20]}
