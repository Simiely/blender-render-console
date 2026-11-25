# -*- coding: utf-8 -*-
r"""多工程队列 —— 按顺序渲染多个工程的任务清单。

与 taskstore 的分工：

| 文件 | 回答的问题 | 粒度 |
|---|---|---|
| `taskstore`（pending.json） | 当前**这个**任务是什么、能不能续 | 单个任务 |
| `queue`（queue.json） | 还有**哪些**任务要跑、跑到第几个 | 多个任务 |
| `state`（.render_state.json） | 当前这个任务跑到第几帧 | 单个任务 |

**为什么不复用 taskstore**：它的注释明确写着"只保留一个当前任务"（渲染独占 GPU，
排队并发没意义）。队列是新增的一层，语义不同 —— 一个队列含 N 个任务项，每项有自己的
config / blender 路径 / 断点，逐个跑。现有的 pending.json 继续表示"当前正在跑的那一项"，
这样**旧的开机续跑逻辑一行都不用改**（它认领 pending 即可），队列只是在任务跑完后
负责把下一项写进 pending。

状态机（每项）：
    pending → running → done / failed
    - 崩溃/掉电：running 原样留着（同 taskstore 反向设计），下次开机继续
    - 主动停止：running 项标记 autoresume=False（写进 pending），其余项保持 pending
    - 全部 done：队列清空（queue.json 删除）
"""

import json
import os
import tempfile
import time

from . import taskstore

STORE_VERSION = 1
FILENAME = "queue.json"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def queue_path():
    """与 pending.json 同目录（%LOCALAPPDATA%\\blender-render-console）。"""
    return os.path.join(taskstore.store_dir(), FILENAME)


def _atomic_write(data, path):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".brc-queue-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def new_item(cfg, blender_exe):
    """把一份 JobConfig 包成队列项（必须可 JSON 序列化）。"""
    return {
        "id": "%d-%s" % (int(time.time() * 1000), os.path.basename(cfg.blend)),
        "status": STATUS_PENDING,
        "blender_exe": blender_exe or "",
        "config": cfg.to_dict(),
        "added": round(time.time(), 3),
    }


def load():
    """读队列。不存在 / 坏掉 / 版本不对都返回 None（宁可当没有）。"""
    path = queue_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            q = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(q, dict) or not isinstance(q.get("items"), list):
        return None
    if q.get("version") != STORE_VERSION:
        return None
    q.setdefault("current", 0)
    q.setdefault("autoresume", True)
    return q


def save(items, current=0, autoresume=True):
    """整份写入（原子）。返回队列文件路径。"""
    path = queue_path()
    _atomic_write({
        "version": STORE_VERSION,
        "created": round(time.time(), 3),
        "updated": round(time.time(), 3),
        "current": int(current),
        "autoresume": bool(autoresume),
        "items": items,
    }, path)
    return path


def clear():
    try:
        os.remove(queue_path())
        return True
    except OSError:
        return False


def add(cfg, blender_exe):
    """追加一项，返回（队列 dict, 新项）。"""
    q = load() or {"items": [], "current": 0, "autoresume": True}
    item = new_item(cfg, blender_exe)
    q["items"].append(item)
    save(q["items"], q.get("current", 0), q.get("autoresume", True))
    return q, item


def remove(item_id):
    q = load()
    if not q:
        return False
    before = len(q["items"])
    q["items"] = [it for it in q["items"] if it.get("id") != item_id]
    if len(q["items"]) == before:
        return False
    # current 别越界
    q["current"] = min(q.get("current", 0), max(0, len(q["items"]) - 1))
    if q["items"]:
        save(q["items"], q["current"], q.get("autoresume", True))
    else:
        clear()
    return True


def move(item_id, delta):
    """上移/下移一项（delta = -1 / +1）。返回是否真的移动了。"""
    q = load()
    if not q:
        return False
    items = q["items"]
    idx = next((i for i, it in enumerate(items) if it.get("id") == item_id), None)
    if idx is None:
        return False
    j = idx + delta
    if j < 0 or j >= len(items):
        return False
    items[idx], items[j] = items[j], items[idx]
    save(items, 0, q.get("autoresume", True))   # 重排后从头算（避免 current 指错人）
    return True


def set_status(item_id, status):
    """更新某项状态（running / done / failed）。"""
    q = load()
    if not q:
        return False
    for it in q["items"]:
        if it.get("id") == item_id:
            it["status"] = status
            break
    else:
        return False
    save(q["items"], q.get("current", 0), q.get("autoresume", True))
    return True


def update_config(item_id, cfg, blender_exe=None):
    """刷新某一项的**配置快照**（自动读工程配置完成后必须调，否则后续用旧值）。

    传 `JobConfig` 或 `to_dict()` 的结果都行；`blender_exe=None` 表示不动原来的
    程序路径。状态、排序、current 一律原样保留 —— 这里只换快照，不做事。
    """
    q = load()
    if not q:
        return False
    data = cfg.to_dict() if hasattr(cfg, "to_dict") else dict(cfg or {})
    for it in q["items"]:
        if it.get("id") == item_id:
            it["config"] = data
            if blender_exe is not None:
                it["blender_exe"] = blender_exe or ""
            save(q["items"], q.get("current", 0), q.get("autoresume", True))
            return True
    return False


def set_autoresume(flag):
    """整份队列的开机续跑旗标（与界面「启动时自动续跑」勾选同步）。

    `set_current()` 写 pending.json 时直接取这个值 —— 只改 pending 不改队列的话，
    下一项又会用回旧旗标，界面勾选等于只对当前项有效（坑 35 的同款）。
    队列文件不存在时返回 False（没有队列可改，不算错误）。
    """
    q = load()
    if not q:
        return False
    save(q["items"], q.get("current", 0), bool(flag))
    return True


def set_current(item_id):
    """把某项设为当前（current 索引），并把它的 config 写进 pending.json
    供现有开机续跑逻辑认领；返回写入的 pending 存档。"""
    q = load()
    if not q:
        return None
    idx = next((i for i, it in enumerate(q["items"]) if it.get("id") == item_id), None)
    if idx is None:
        return None
    q["current"] = idx
    q["items"][idx]["status"] = STATUS_RUNNING
    save(q["items"], idx, q.get("autoresume", True))
    item = q["items"][idx]
    from .core import JobConfig
    cfg = JobConfig.from_dict(item["config"])
    path = taskstore.save(cfg, item.get("blender_exe") or "",
                          autoresume=q.get("autoresume", True),
                          reason="队列任务")
    return path


def next_pending():
    """返回第一个非 done 的项（供"跑完自动进下一项"）。"""
    q = load()
    if not q:
        return None
    for it in q["items"]:
        if it.get("status") != STATUS_DONE:
            return it
    return None


def summary(q=None):
    """给界面显示的一行摘要。"""
    q = q or load()
    if not q or not q.get("items"):
        return "队列为空"
    items = q["items"]
    done = sum(1 for it in items if it.get("status") == STATUS_DONE)
    failed = sum(1 for it in items if it.get("status") == STATUS_FAILED)
    return "共 %d 项 · 完成 %d · 失败 %d · 当前 %d" % (
        len(items), done, failed, q.get("current", 0) + 1)
