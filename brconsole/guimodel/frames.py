# -*- coding: utf-8 -*-
"""帧段解析、展开、计数与实时回显。

这一组规则的唯一变更原因是"用户怎么填帧、界面怎么回显帧"。
渲染侧只拿 `segments_to_frames()` 的结果，回显侧只拿 `frames_preview()`；
两者共用 `_parse_segments()`，避免"回显说 6 帧，真正渲染却是 5 帧"。
"""

import math

from ..core import expand_segment
from .constants import (PREVIEW_MATERIALIZE_LIMIT, PREVIEW_MAX_LIST,
                        PREVIEW_MAX_MERGED, PREVIEW_MAX_SEGMENTS)


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


def _parse_segments(segments, step=1):
    """把帧段归一成 `(起, 止, 步长)` 描述符 + 错误列表，**不展开帧号**。

    语义与 `segments_to_frames` 完全一致，抽出来是为了让"渲染用的全量展开"和
    "界面每敲一个字符就跑的实时回显"共用同一套校验规则：回显说 6 帧，
    真正渲染就必须是那 6 帧，不能两边各写一份解析后悄悄漂开。
    """
    descs, errors = [], []

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
        if s is None or e is None:
            f = e if s is None else s
            descs.append((f, f, 1))
            continue
        a, b = (s, e) if s <= e else (e, s)
        eff = st if st is not None else step
        try:
            if eff < 1:
                raise ValueError("步长必须 ≥ 1，当前：%s" % (eff,))
            descs.append((a, b, eff))
        except ValueError as exc:
            errors.append("第 %d 段：%s" % (i, exc))
    return descs, errors


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
    descs, errors = _parse_segments(segments, step)
    frames = []
    for a, b, s in descs:
        frames.extend(expand_segment(a, b, s))
    seen, out = set(), []
    for f in frames:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return sorted(out), errors


def _ap_intersection(descs):
    """一组等差数列的交集帧数；无解返回 0（中国剩余定理）。

    两个段 `1-9 每 2 帧` 与 `1-9 每 3 帧` 的交集是 `1,7`：
    同余式有解当且仅当起点差能被步长 gcd 整除，解模 lcm 唯一。
    用它做容斥，才能在**不展开帧号**的前提下数清重叠段去重后的总数。
    """
    a0, b0, m = descs[0]
    r, lo, hi = a0, a0, b0
    for a, b, s in descs[1:]:
        lo, hi = max(lo, a), min(hi, b)
        g = math.gcd(m, s)
        if (a - r) % g:
            return 0
        m_g, s_g = m // g, s // g
        t = ((a - r) // g * pow(m_g, -1, s_g)) % s_g
        r = (r + m * t) % (m * s_g)
        m = m * s_g
    if lo > hi:
        return 0
    k0 = (lo - r + m - 1) // m
    k1 = (hi - r) // m
    return max(0, k1 - k0 + 1)


def _ap_union_count(descs, segments=None, step=1):
    """展开后**去重**的帧数，不物化帧号（实时回显用）。

    段数 ≤ 12 时走容斥 + CRT，代价 O(2^段数)，与帧数无关；
    段数更多时退回全量展开（界面上极少见，此时本来就该提示用户简化）。
    """
    if not descs:
        return 0
    if len(descs) > 12:
        return len(segments_to_frames(segments, step)[0])
    total = 0
    for mask in range(1, 1 << len(descs)):
        subset = [descs[i] for i in range(len(descs)) if mask & (1 << i)]
        n = _ap_intersection(subset)
        if n:
            total += n if bin(mask).count("1") % 2 else -n
    return max(0, total)


def frame_count(segments, step=1):
    """帧段 → `(去重后的帧数, 错误列表)`，不物化帧号。"""
    descs, errors = _parse_segments(segments, step)
    return _ap_union_count(descs, segments, step), errors


def _capped_frames(descs, cap):
    """从描述符里取前 `cap` 个帧号，供错误回显/超大范围回显用。"""
    out = []
    for a, b, s in descs:
        while a <= b and len(out) < cap:
            out.append(a)
            a += s
        if len(out) >= cap:
            break
    return out


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


def frames_preview(segments, step=1):
    """帧段列表 → `(帧号列表, 回显文本, 是否合法)`。

    界面每改一个字符就调它一次：合法 → 绿字「共 N 帧：…」，
    不合法 → 红字「！第 2 段：…」。**按下「开始渲染」前就能看出问题在哪**，
    而不是等弹框。

    性能约束：回显帧数用 `frame_count()` 按等差数列容斥算，与帧数无关；
    只有帧数不多时才物化帧号给"合并后 1-240"那句紧凑对账。大范围回显里
    帧号列表最多给 `PREVIEW_MAX_LIST` 个示例，真正的全量展开只发生在
    点「开始渲染」之后的 `segments_to_frames()`。
    """
    descs, errors = _parse_segments(segments, step)
    count = _ap_union_count(descs, segments, step)
    if errors:
        return _capped_frames(descs, PREVIEW_MAX_LIST), "！ " + "；".join(errors), False
    if not count:
        return [], "！ 还没有可渲染的帧 —— 至少填一段的起帧", False

    frames = (segments_to_frames(segments, step)[0]
              if count <= PREVIEW_MATERIALIZE_LIMIT
              else _capped_frames(descs, PREVIEW_MAX_LIST))
    labels = [segment_label(sg, step) for sg in (segments or [])]
    labels = [x for x in labels if x != "（未填）"]
    if len(labels) > PREVIEW_MAX_SEGMENTS:
        labels = labels[:PREVIEW_MAX_SEGMENTS] + ["…"]
    body = " · ".join(labels)
    if len(labels) > 1:
        # 多段时再补一句"合并后是什么"：两个段**重叠或首尾相接**时，光看每段看不出
        # 最后究竟渲哪些帧（重复的帧会被去掉、只渲一次）。这一句就是那份对账。
        if count <= PREVIEW_MATERIALIZE_LIMIT:
            merged = frames_to_text(frames)
            if len(merged) <= PREVIEW_MAX_MERGED:
                body += "；合并后 %s" % merged
        else:
            body += "；合并后 %d 帧（帧号太多，略）" % count
    elif count <= PREVIEW_MAX_LIST:
        body += "（%s）" % ", ".join(str(f) for f in frames)
    return frames, "共 %d 帧：%s" % (count, body), True
