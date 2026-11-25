# -*- coding: utf-8 -*-
"""GUI 纯逻辑层的常量。

这里只放"下拉文案 ↔ 实际值"和预览上限这类**没有行为**的单一来源；
任何带判断的规则都放 `form.py` / `frames.py` / `events.py`。
"""

KEEP = ""          # 下拉框里"保持工程设置"对应的实际值就是空

# 场景下拉的两个占位文案（都不是真实场景名，交给 form.scene_to_config 过滤掉）
SCENE_DEFAULT_LABEL = "（用工程默认场景）"
SCENE_NEED_READ_LABEL = "（先读取工程配置）"
SCENE_PLACEHOLDERS = (SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL)

# ---------------- 失败重试：一个下拉，一个次数 ----------------
#
# 界面上只有**一个下拉**，它同时决定三个互相牵制的内核参数：
#     (最多重启次数, 单帧最多尝试, 连续无进展轮数)
#
# 规则只有一条 —— **选中的次数同时作用于重启和单帧尝试**（用户原话：
# "如果选一直重复，就是所有的都一直重复；如果选 3 次，那其他的尝试也是三次"）：
#
#     一直重复，直到渲完  →  重启不限、单帧不限
#     3 次               →  最多重启 3 次、单帧最多试 3 次
#
# 拆成「下拉 + 两个数字框」时，用户从界面上看不出它们是一组的：为什么要填两个数、
# "填 0 等于关掉哪道闸门"，全靠两行长说明文字解释。那两行说明本身就是复杂度外溢。
#
# 「不限」沿用 core/state 里**既有**的约定（`JobState.remaining` 用
# `max_attempts <= 0` 表示不限），不需要给内核加新语义。
#
# ⚠️ 无进展兜底**永远**是 3 轮，不跟这个下拉走：它和"重试次数"不是一回事 ——
#    它管的是"再试也没意义"的判据（连续几轮一帧都没推进）。跟着变成不限就等于把
#    内核里唯一的防死循环闸门关掉（AGENTS 坑 39）：卡在同一处反复崩、一帧都渲不出来时，
#    没有任何条件能让它停下来。
DEFAULT_NO_PROGRESS = 3

RETRY_UNLIMITED_LABEL = "一直重复，直到渲完"
#: 下拉里可选的有限次数（顺序即显示顺序）
RETRY_COUNTS = (1, 3, 5, 10)
RETRY_CHOICES = (RETRY_UNLIMITED_LABEL,) + tuple("%d 次" % n for n in RETRY_COUNTS)
#: 默认「一直重复」——与 v0.8.1 起的默认行为一致
DEFAULT_RETRY = RETRY_UNLIMITED_LABEL

#: 第一段帧范围的默认起止（也是"没读到工程配置"时的兜底），界面与 FormModel 共用一份
DEFAULT_SEGMENT_START = "1"
DEFAULT_SEGMENT_END = "250"

ENGINES = [("保持工程设置（用 .blend 里的）", KEEP), ("Cycles", "CYCLES"),
           ("EEVEE", "BLENDER_EEVEE"), ("Workbench", "BLENDER_WORKBENCH")]
DEVICES = [("保持工程设置", KEEP), ("CPU", "CPU"), ("CUDA", "CUDA"),
           ("OptiX", "OPTIX"), ("HIP", "HIP"), ("oneAPI", "ONEAPI")]
# ---------------- 输出格式与图片位数 ----------------
#
# 表结构：`(identifier, 可用位数, 推荐默认位数, 扩展名)`，**一张表撑起下拉、校验、
# 扩展名替换**三件事 —— 别再另开一张"显示值 → 实际值"的映射表（那种两表一定漂，
# 见 AGENTS 坑 31）。
#
# ⚠️ 这里每一位都是 **Blender 5.2.2 运行时逐个实测**得到的（`file_format = f` 之后
#    逐个试 `color_depth = d` 并读回确认），不是照抄文档：官方手册在 Cineon
#    （写 8/10/12/16）与 Iris（写 8/16）两处与实测不符 —— 5.2.2 里 Cineon 只认 10、
#    Iris 只认 8。写错了不会报错，只会被 Blender 静默拒绝（"选了 16 位还是 8 位"）。
#    复现命令见 AGENTS「常用命令」里的 `tools/probe_depth.py`。
FORMAT_SPECS = [
    ("PNG", ("8", "16"), "8", "png"),
    ("OPEN_EXR", ("16", "32"), "32", "exr"),
    ("JPEG", ("8",), "8", "jpg"),
    ("TIFF", ("8", "16"), "8", "tif"),
    ("WEBP", ("8",), "8", "webp"),
    ("AVIF", ("8", "10", "12"), "8", "avif"),
    ("BMP", ("8",), "8", "bmp"),
    ("TARGA", ("8",), "8", "tga"),
    ("TARGA_RAW", ("8",), "8", "tga"),
    ("DPX", ("8", "10", "12", "16"), "10", "dpx"),
    ("JPEG2000", ("8", "12", "16"), "8", "jp2"),
    ("CINEON", ("10",), "10", "cin"),
    ("HDR", ("32",), "32", "hdr"),
    ("IRIS", ("8",), "8", "rgb"),
]

#: 下拉候选：第一项是「保持工程设置」（identifier 为空 = 不碰工程）
FORMATS = [(KEEP, (), None, "")] + FORMAT_SPECS

#: 模板里"已经算扩展名"的后缀（规范后缀 + 常见另一种写法，与 driver 的
#: `KNOWN_EXT_ALIASES` 同一口径；两边由 `test_driver_contract` 锁在一起）
KNOWN_EXT_ALIASES = ("jpeg", "tiff")

#: 格式下拉里「保持工程设置」的显示文案（实际值仍是空串 `KEEP`）
FORMAT_KEEP_LABEL = "保持工程设置（用 .blend 里的）"
#: 位数下拉在「保持工程设置」时的显示文案（没有可选位数，控件同时会被禁用）
DEPTH_KEEP_LABEL = "随工程"

#: 回显里最多列几段就省略（多了会撑破那一行）
PREVIEW_MAX_SEGMENTS = 4
#: 合并后的紧凑写法最多留多少个字符（`1-2500,3000-3200` 这种）
PREVIEW_MAX_MERGED = 60
#: 单段且帧数不超过这个数时，回显里把帧号全列出来
PREVIEW_MAX_LIST = 12
#: 超过这个帧数就不再物化帧号做紧凑回显 —— 界面每敲一个字符都会调这里，
#: 不能因为用户填了 1-1000000 就让主线程每次分配几十 MB（实测 0.4s / 91MB）
PREVIEW_MATERIALIZE_LIMIT = 20000

# ---------------- 任务结束后的善后（关机 / 睡眠） ----------------
AFTERMATH_NOTHING = "nothing"
AFTERMATH_SHUTDOWN = "shutdown"
AFTERMATH_SLEEP = "sleep"
AFTERMATH_VALUES = (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN, AFTERMATH_SLEEP)
#: 下拉显示文案 ↔ 实际值（单一来源，界面层只拿它填 Combobox）
AFTERMATH_LABELS = [("什么都不做", AFTERMATH_NOTHING),
                    ("关机（60 秒倒计时，可取消）", AFTERMATH_SHUTDOWN),
                    ("睡眠", AFTERMATH_SLEEP)]
AFTERMATH_BY_LABEL = {label: value for label, value in AFTERMATH_LABELS}
