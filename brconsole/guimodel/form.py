# -*- coding: utf-8 -*-
"""表单字段、输出格式与场景详情 → `JobConfig` 的纯逻辑。

这一组的唯一变更原因是"用户填的表单怎么变成一次渲染任务"。
所有校验、补全、文案 ↔ 实际值转换都在这里，界面只负责把结果贴到控件上。
"""

import os

from ..core import UNLIMITED, complete_output_template, parse_restart_limit
from .constants import (DEFAULT_NO_PROGRESS, DEFAULT_RETRY, DEFAULT_SEGMENT_END,
                        DEFAULT_SEGMENT_START, DEPTH_KEEP_LABEL, DEVICES, ENGINES,
                        FORMAT_KEEP_LABEL, FORMATS, KNOWN_EXT_ALIASES, KEEP,
                        RETRY_UNLIMITED_LABEL, SCENE_DEFAULT_LABEL,
                        SCENE_PLACEHOLDERS)
from .frames import (frames_to_segments, new_segment, normalise_segment,
                     segments_to_frames)


def retry_count(label):
    """下拉文案 → 次数（`UNLIMITED` = 不限）。

    解析交给 `core.parse_restart_limit`（它认 `"3 次"` → 3、`"一直重复…"` → -1），
    认不出来时按**有界**那侧兜 —— 宁可少试几次，也别悄悄把次数变成无限。
    """
    return parse_restart_limit(str(label or "").strip(), default=5)


def retry_label_for(count):
    """次数 → 下拉文案。预设里没有的数值也如实显示（老存档里可能有"7 次"）。"""
    n = int(count)
    return RETRY_UNLIMITED_LABEL if n < 0 else "%d 次" % n


def retry_note(label):
    """下拉旁边那一句短说明：只说"这一次到底放宽/收紧了什么"。"""
    n = retry_count(label)
    head = "重启与单帧尝试都不限" if n < 0 else "重启与单帧都按 %d 次" % n
    return "%s · 连续 %d 轮没推进仍会停" % (head, DEFAULT_NO_PROGRESS)


def retry_plan(label):
    """下拉文案 → `(max_restarts, max_frame_attempts, max_no_progress_rounds)`。

    这是**唯一**一份"三个重试参数从哪来"的规则：`FormModel.to_config` 调它，
    界面只负责把下拉的文案读出来。别在别处再拼一遍 —— 拼两遍就一定会漂。
    """
    n = retry_count(label)
    if n < 0:
        return (UNLIMITED, 0, DEFAULT_NO_PROGRESS)
    return (n, n, DEFAULT_NO_PROGRESS)


def retry_label_from_config(cfg_dict):
    """存档里的参数 → 下拉文案（开机续跑回填用）。

    只读 `max_restarts`：它就是这一版里"那个次数"，重启与单帧尝试都从它来。
    老存档里可能是 7、20 这类预设里没有的数 —— 照样显示成"N 次"，不折叠成预设值，
    否则"回填一下行为就变了"。
    """
    d = cfg_dict or {}
    return retry_label_for(parse_restart_limit(d.get("max_restarts", 5), default=5))


def format_label(ident):
    """格式 identifier → 下拉显示文案（空值显示成「保持工程设置…」）。

    为什么只做一个 `format_label` / `format_from_label` 的小函数对，而不是再抄一张
    「文案 → 值」的表：下拉里显示的**其余项就是 identifier 本身**（PNG / OPEN_EXR …），
    它既是显示值又是下发值，永远不会漂（AGENTS 坑 31）。
    """
    ident = str(ident or "").strip()
    return ident.upper() if ident else FORMAT_KEEP_LABEL


def format_from_label(label):
    """下拉显示文案 → identifier（「保持工程设置…」→ 空串 = 不碰工程）。"""
    s = str(label or "").strip()
    return "" if (not s or s == FORMAT_KEEP_LABEL) else s.upper()


def depth_choices(fmt):
    """位数下拉的候选：格式是「保持工程设置」时只有一个占位文案。"""
    depths = depths_for(fmt)
    return list(depths) if depths else [DEPTH_KEEP_LABEL]


def depth_value(label):
    """位数下拉的显示值 → 实际下发值（占位文案 → 空串 = 不碰工程）。"""
    s = str(label or "").strip()
    return "" if s == DEPTH_KEEP_LABEL else s


def format_spec(fmt):
    """格式 identifier → `(可用位数, 推荐默认, 扩展名)`；未知/空返回 `((), None, "")`。"""
    want = str(fmt or "").strip().upper()
    for ident, depths, default, ext in FORMATS:
        if ident == want:
            return depths, default, ext
    return (), None, ""


def depths_for(fmt):
    """这个格式**实际接受**的位数（白名单，按实测值）。"""
    return format_spec(fmt)[0]


def default_depth_for(fmt):
    """这个格式的推荐默认位数（空/未知格式返回 None）。"""
    return format_spec(fmt)[1]


def format_ext_for(fmt):
    """这个格式落盘用的扩展名（空/未知返回空串）。"""
    return format_spec(fmt)[2]


def coerce_depth(fmt, depth):
    """把一个位数取值收敛到 `fmt` 的白名单里，返回 `(实际位数, 是否被改过)`。

    为什么"空"也要落到推荐默认：实测**换格式本身就会改位数**
    （PNG 从 EXR 切过去会变成 16 位），只下发格式不下发位数就是"位数悄悄变了"；
    界面把收敛后的值显示出来，用户看得见。
    格式是「保持工程设置」时位数一律置空 —— 说了不碰，就一点都不碰。
    """
    want = str(fmt or "").strip().upper()
    if not want:
        return "", bool(depth)
    depths, default, _ext = format_spec(want)
    d = str(depth or "").strip()
    if d in depths:
        return d, False
    return (default or ""), True


def retarget_extension(template, fmt):
    """把输出模板末尾的**已知图像扩展名**换成新格式的扩展名。

    只动认得出的图像后缀（`f_####.png` → `f_####.exr`）：`driver.output_path_for()`
    的规则是「模板自带扩展名优先」，模板写 `.png` 却选了 EXR 时，会写出一个叫
    `.png` 的 EXR 文件 —— 不报错、只是事后谁都不敢认。模板没有扩展名、或后缀不是
    已知图像格式时**原样返回**，不去猜用户的自定义命名。
    """
    ext = format_ext_for(fmt)
    if not ext:
        return template
    root, cur = os.path.splitext(template or "")
    known = {e for _i, _d, _def, e in FORMATS if e} | set(KNOWN_EXT_ALIASES)
    if not cur or cur.lstrip(".").lower() not in known:
        return template
    if cur.lstrip(".").lower() == ext:
        return template
    return root + "." + ext


# ---------------- 集合显示：界面勾选 ↔ 内部 hidden 列表 ----------------
#
# 界面上「集合显示」勾选 = 渲染时显示；取消勾选 = 渲染时隐藏。
# 但底层（core/state/driver/JobConfig）沿用的字段名一直是 `hidden_collections`，
# 存的也确实是「要隐藏的集合名」。两边语义相反，**只在界面边界转换一次**：
# 别去反转存储结构（断点签名、driver 都依赖它），也别在 gui.py 里再写一遍转换。

def hidden_from_checks(names, checked):
    """`(工程里的全部集合名, 界面上勾选的名字)` → 要隐藏的名字列表。

    界面勾选 = 显示，所以没勾的那些才是 hidden。顺序跟随 `names`，
    不跟随 `checked`（集合清单的顺序由工程决定，稳定可比对）。
    """
    shown = set(checked or ())
    return [str(n) for n in (names or ()) if str(n) not in shown]


def checks_from_hidden(names, hidden):
    """`(工程里的全部集合名, hidden 列表)` → 界面上该勾选的名字列表。

    存档/队列项回填走这里：`cfg.hidden` 是隐藏列表，复选框要显示成反过来的勾选态。
    """
    hidden_set = set(str(n) for n in (hidden or ()))
    return [str(n) for n in (names or ()) if str(n) not in hidden_set]


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
    from ..inspect import output_template_from

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
    # 4.2+ 把 EEVEE 的 identifier 改成了 BLENDER_EEVEE_NEXT，而下拉里只有 EEVEE 一项
    # （driver.ENGINE_ALIASES 讲的是同一件事，但那是渲染侧脚本，界面模型不该反过来依赖它）。
    # 不归一化的话读出来的引擎会被当成"不认识"丢掉，界面继续显示上一个值。
    if engine == "BLENDER_EEVEE_NEXT":
        engine = "BLENDER_EEVEE"
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
    if fmt in [f[0] for f in FORMATS]:
        out["file_format"] = fmt
        # 位数只在"该格式确实支持"时才回填 —— 工程里存着一个这个格式不认的值时，
        # 填进下拉会让界面说谎（下拉里根本没有那一项）
        depth = str(detail.get("color_depth") or "").strip()
        if depth in depths_for(fmt):
            out["color_depth"] = depth
    tpl = output_template_from(detail.get("output_path"), blend)
    if tpl:
        out["output"] = tpl
    return out


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
        #: 图片位数（"8"/"16"/"32"…）；空 = 不碰工程设置。
        #: 与 `file_format` 联动：格式是 KEEP 时这里恒为空，由 `to_config` 保证。
        self.color_depth = kw.get("color_depth", KEEP)
        self.scene = kw.get("scene", SCENE_DEFAULT_LABEL)   # 场景名，或占位文案
        #: **要隐藏**的集合名列表（与界面勾选相反：勾上=显示，不勾=隐藏）。
        #: 字段名与存储语义都不能改 —— 断点签名/driver 都按 hidden 列表解释它。
        #: 界面勾选态用 hidden_from_checks()/checks_from_hidden() 在边界处转换。
        self.hidden_collections = list(kw.get("hidden_collections") or ())
        self.resume = kw.get("resume", True)
        #: 重试策略**只有一个字段**（下拉的文案）：选「一直重复」= 重启与单帧尝试都不限；
        #: 选「3 次」= 两者都是 3 次。三个内核参数由 `retry_plan()` 从这一个文案推出来，
        #: 界面上不再有「单帧最多尝试 / 连续无进展」两个可填的框。
        self.retry = kw.get("retry", DEFAULT_RETRY)
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
            color_depth=("" if d.get("color_depth") is None
                         else str(d["color_depth"])),
            scene=d.get("scene") or SCENE_DEFAULT_LABEL,
            hidden_collections=d.get("hidden_collections") or (),
            resume=bool(d.get("resume", True)),
            # 存档里的次数回填到那个下拉（判据在 retry_label_from_config）：
            # 老存档读进来照样跑成原来那样，预设里没有的数值也如实显示
            retry=retry_label_from_config(d),
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
        from ..core import JobConfig

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

        # ---- 输出格式 / 图片位数 ----
        # 两条都只在"用户确实指定了格式"时才下发：格式=「保持工程设置」时位数一律
        # 置空，绝不偷偷改工程的位数（说了不碰就一点都不碰）。
        fmt = str(self.file_format or "").strip().upper()
        depth = str(self.color_depth or "").strip()
        if not fmt:
            depth = ""
        else:
            depths, _default, _ext = format_spec(fmt)
            if not depths:
                errors.append("未知的输出格式：%s" % fmt)
            elif not depth:
                depth, _changed = coerce_depth(fmt, "")
            elif depth not in depths:
                errors.append("%s 不支持 %s 位（可选：%s）"
                              % (fmt, depth, " / ".join(depths)))
        if errors:
            return None, errors, warns

        max_restarts, attempts, no_progress = retry_plan(self.retry)

        cfg = JobConfig(
            blend=blend, frames=frames, output_template=output,
            engine=self.engine or None, samples=samples,
            device=self.device or None, resolution=resolution,
            resolution_percentage=pct, file_format=fmt or None,
            color_depth=depth or None,
            scene=scene_to_config(self.scene),
            hidden_collections=list(self.hidden_collections),
            state_path=None, log_path=None,
            max_restarts=max_restarts,
            max_frame_attempts=attempts,
            resume=bool(self.resume),
            extra_args=self.extra_args,
            restart_delay=self.restart_delay,
            max_no_progress_rounds=no_progress)
        return cfg, errors, warns

    def as_dict(self):
        return dict(self.__dict__)
