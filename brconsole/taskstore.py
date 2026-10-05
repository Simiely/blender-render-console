# -*- coding: utf-8 -*-
"""待办任务的存档 —— 「开机后自动接着渲」的全部依据。

与 state.py 的分工：

| 文件 | 回答的问题 | 谁写 | 何时写 |
|---|---|---|---|
| `state.py`（`.render_state.json`） | 这个任务**跑到哪了** | 渲染循环 | 每渲完一帧 |
| `taskstore`（`pending.json`） | 这个任务**是什么** | 界面 | 点开始 / 跑完 / 点停止 |

**为什么不能只靠 state**：state 只是 JobConfig 的**子集** —— 它没有 `blender_exe`
（那是 RenderJob 的构造参数，压根不在 JobConfig 里），也没有 `max_frame_attempts` /
`max_no_progress_rounds` / `resolution_percentage`。光有它重建不出一个能跑的任务。

**为什么是「默认 True，只有主动停止才置 False」**：进程被强杀、系统掉电时**根本来不及
写文件**，所以「崩溃时打个标记」这种正向设计做不到。反向设计才行得通：

- 崩溃 / 掉电 → 存档原封不动 → 下次开机自动续跑 ✅
- 用户点「停止」→ 置 False → 不偷跑，但断点仍在，可手动继续 ✅
- 正常渲完 → 直接删档 ✅
"""

import json
import os
import tempfile
import time

from .core import JobConfig
from .state import JobState

STORE_VERSION = 1
FILENAME = "pending.json"
APP_DIRNAME = "blender-render-console"


def store_dir():
    """存档目录：`%LOCALAPPDATA%\\blender-render-console`（非 Windows 退回家目录）。

    刻意**不放输出目录旁边**：开机自启时还不知道任务在哪，
    必须有一个跟工程/输出无关的固定位置能问出「有没有待办任务」。
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_DIRNAME)


def pending_path():
    return os.path.join(store_dir(), FILENAME)


# ---------------- 存 / 取 ----------------
def save(cfg, blender_exe, autoresume=True):
    """写入（或覆盖）当前待办任务。原子落盘，返回存档路径。

    ⚠️ 覆盖是**有意的**：只保留一个"当前任务"。渲染是独占 GPU 的重活，
    排队多个任务既没法并发也没意义，反而让「开机后续哪个」变得不确定。
    """
    path = pending_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rec = {
        "version": STORE_VERSION,
        "created": round(time.time(), 3),
        "updated": round(time.time(), 3),
        "blender_exe": blender_exe or "",
        "autoresume": bool(autoresume),
        "config": cfg.to_dict(),
    }
    fd, tmp = tempfile.mkstemp(prefix=".brc-task-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def load():
    """读存档。不存在 / 坏掉 / 版本不认识都返回 None（宁可当没有，也别让程序起不来）。"""
    path = pending_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("config"), dict):
        return None
    if rec.get("version") != STORE_VERSION:
        return None
    rec.setdefault("autoresume", True)
    rec.setdefault("blender_exe", "")
    return rec


def clear():
    """删掉存档（任务正常结束）。删不掉不抛异常 —— 顶多多一次空跑。"""
    try:
        os.remove(pending_path())
        return True
    except OSError:
        return False


def set_autoresume(flag):
    """改存档里的自动续跑标记。返回是否真的改到了（没存档就返回 False）。

    只改这一个字段、**不整份重写**，是为了不碰 config —— 万一用户改了界面上的参数
    但还没点开始，存档里的任务定义仍应是"上次真正跑的那个"。
    """
    rec = load()
    if rec is None:
        return False
    rec["autoresume"] = bool(flag)
    rec["updated"] = round(time.time(), 3)
    path = pending_path()
    fd, tmp = tempfile.mkstemp(prefix=".brc-task-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ---------------- 判断 ----------------
def config_of(rec):
    """把存档还原成 JobConfig。坏数据返回 None（不抛，开机路径上不能炸）。"""
    try:
        cfg = JobConfig.from_dict(rec.get("config") or {})
    except (TypeError, ValueError):
        return None
    if not cfg.blend or not cfg.output_template or not cfg.frames:
        return None
    return cfg


def progress_of(cfg):
    """读断点文件，返回 {done,total,remaining,complete}；没有断点返回 None。"""
    st = JobState.load(cfg.state_file())
    if st is None:
        return None
    # 断点属于别的任务（换了工程/场景/帧范围）→ 对我们来说等于"还没开始"
    if (os.path.abspath(st.blend) != os.path.abspath(cfg.blend)
            or st.output_template != cfg.output_template
            or tuple(st.frames) != tuple(cfg.frames)
            or (st.scene or "") != (cfg.scene or "")):
        return None
    complete = st.is_complete()
    return {
        "done": st.done_count,
        "total": st.total,
        "failed": len(st.failed),
        "complete": complete,
    }


def check_runnable(cfg, blender_exe):
    """开机续跑前的体检。返回 (ok, reason) —— 不 ok 时 reason 直接给用户看。"""
    if not os.path.exists(cfg.blend):
        return False, "工程文件不在了：%s" % cfg.blend
    if not blender_exe or not os.path.exists(blender_exe):
        return False, "blender.exe 不在了：%s" % (blender_exe or "(未记录)")
    if "####" not in cfg.output_template:
        return False, "输出模板缺少 #### 占位符：%s" % cfg.output_template
    return True, ""


def resume_decision(rec=None):
    """开机时该拿这个存档怎么办。

    返回 (action, reason)：

    - `"run"`    未完成且允许自动续跑 → 直接跑
    - `"prompt"` 未完成但用户主动停过 → 回填表单，等用户点开始
    - `"done"`   已经渲完了 → 清理存档
    - `"drop"`   存档坏了 / 前置条件不满足 → 清理（或保留？见下）
    """
    rec = rec if rec is not None else load()
    if rec is None:
        return "none", ""
    cfg = config_of(rec)
    if cfg is None:
        return "drop", "存档内容不完整，已忽略"
    blender = rec.get("blender_exe") or ""
    ok, why = check_runnable(cfg, blender)
    if not ok:
        # ⚠️ 这里**不删档**：blender 位置变了、工程被移走了都是可恢复的
        # （用户可能只是插拔了移动硬盘），删了他就再也找不回这次任务了。
        return "drop", why
    prog = progress_of(cfg)
    if prog and prog["complete"]:
        return "done", ""
    if not rec.get("autoresume"):
        return "prompt", ""
    return "run", ""


def describe(rec):
    """一行话描述存档内容（界面提示用）。"""
    cfg = config_of(rec) if rec else None
    if cfg is None:
        return "（存档无法解析）"
    prog = progress_of(cfg)
    bits = [os.path.basename(cfg.blend) or cfg.blend]
    if cfg.scene:
        bits.append("场景 %s" % cfg.scene)
    bits.append("%d 帧" % len(cfg.frames))
    if prog:
        bits.append("已完成 %d/%d" % (prog["done"], prog["total"]))
    else:
        bits.append("尚未开始")
    return " · ".join(bits)
