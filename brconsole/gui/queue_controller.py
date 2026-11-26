# -*- coding: utf-8 -*-
"""多工程队列、概览与选中项的控制器 mixin。

由 `brconsole/gui.py` 按变更原因抽出；方法体保持原样，便于逐步迁移到 view 契约。
"""

import os
from tkinter import messagebox

from .. import queue_store, theme
from ..guimodel.constants import DEFAULT_RETRY, DEPTH_KEEP_LABEL, DEVICES, ENGINES
from ..guimodel.form import FormModel, format_from_label
from ..guimodel.frames import segment_label, segments_to_frames
from ..guimodel.stateinfo import guess_state_path, state_summary
from ..inspect import scene_names
from .common import fit_combobox  # noqa: F401


class QueueControllerMixin(object):
    def _selected_item(self):
        """当前右侧展示的队列项（磁盘上的 queue.json 是唯一数据源）。"""
        q = queue_store.load()
        if not q or not q.get("items"):
            return None
        for it in q["items"]:
            if it.get("id") == self._selected_queue_id:
                return it
        return q["items"][0]

    def _refresh_queue(self):
        """重画左栏队列，并把选中项铺到右侧模块（含概览与标题摘要）。"""
        q = queue_store.load()
        items = (q or {}).get("items", [])
        self.lst_queue.delete(0, "end")
        for it in items:
            cfg = it.get("config") or {}
            name = os.path.basename(cfg.get("blend") or "?")
            frames = cfg.get("frames") or []
            # 卡片第二行给「帧数 · 断点进度」——比重复一遍输出文件名有用得多
            # ⚠️ 断点文件是按**输出目录**放的：两项共用同一目录时会读到同一份，
            #    所以只在「总数跟这一项自己的帧数对得上」时才敢显示。
            out = cfg.get("output_template") or ""
            bp = state_summary(guess_state_path(out)) if out else None
            done = ""
            if bp and frames and bp.get("total") == len(frames):
                done = "已完成 %d/%d" % (bp["done"], bp["total"])
            self.lst_queue.insert(
                "end", " %s | %s | %s | %s" % (
                    name, ("%d 帧" % len(frames)) if frames else "?",
                    it.get("status", queue_store.STATUS_PENDING), done))
            status = it.get("status")
            if status == queue_store.STATUS_DONE:
                self.lst_queue.itemconfig("end", foreground=theme.MUTED)
            elif status == queue_store.STATUS_FAILED:
                self.lst_queue.itemconfig("end", foreground=theme.WARN)
        self.lbl_queue_count.configure(text="%d 项" % len(items))
        self.lbl_queue.configure(text=queue_store.summary(q))
        ids = [it.get("id") for it in items]
        if self._selected_queue_id not in ids:
            self._selected_queue_id = ids[0] if ids else None
        if self._selected_queue_id:
            idx = ids.index(self._selected_queue_id)
            self._queue_refreshing = True
            self.lst_queue.selection_clear(0, "end")
            self.lst_queue.selection_set(idx)
            self.lst_queue.see(idx)
            self._queue_refreshing = False
        # 启动/没有任何编辑时：把选中项的配置铺到右侧，别让左边高亮一项、
        # 右边却全是「—」。用户一旦自己填过（v_blend 非空）就不再覆盖。
        if items and not str(self.v_blend.get() or "").strip():
            self._apply_queue_item(self._selected_item())
        self._sync_overview()

    @staticmethod
    def _choice_label(choices, value):
        """(显示文案, 实际值) 表 → 给人看的文案（下拉里存的是实际值）。"""
        return next((label for label, val in choices if val == value),
                    value or "—")

    @staticmethod
    def _short_choice(choices, value):
        """概览里用的短文案：留空一律说「随工程」，别把长标签塞进四列格子。"""
        raw = str(value or "").strip()
        if not raw:
            return "随工程"
        label = next((label for label, val in choices if val == raw), raw)
        return label.split("（")[0]          # 去掉「（用 .blend 里的）」这类括注

    @staticmethod
    def _short_path(path, limit=24):
        """概览里的路径只留最后两段（…\\out\\demo_a_####）：四列格子里放不下全路径。"""
        s = str(path or "")
        parts = s.replace("/", "\\").split("\\")
        tail = "\\".join([p for p in parts[-2:] if p]) if len(parts) > 1 else s
        short = "…\\" + tail if len(parts) > 2 else tail
        if len(short) <= limit + 2:
            return short
        return "…" + short[-(limit + 1):]

    def _sync_overview(self):
        """概览与四个标题摘要：标题栏永远显示当前值，不用展开模块去找。"""
        if not self._overview_tags:
            return
        self._collect_form()
        segs = self._read_segments()
        frames, _errors = segments_to_frames(segs)
        hidden = [n for n, v in self._collection_vars.items() if not v.get()]
        cfg = (self._selected_item() or {}).get("config") or {}
        bp = state_summary(guess_state_path(self.v_output.get()))
        bp_text = ("%d/%d" % (bp["done"], bp["total"])) if bp else "无"
        samples = str(self.v_samples.get() or "").strip()
        engine = self._short_choice(ENGINES, self.v_engine.get())
        fmt_ident = format_from_label(self.v_format.get())
        fmt = fmt_ident.upper() if fmt_ident else "随工程"
        depth = str(self.v_depth.get() or "").strip()
        if fmt != "随工程" and depth and depth != DEPTH_KEEP_LABEL:
            fmt = "%s · %s 位" % (fmt, depth)
        w, h = str(self.v_w.get() or "").strip(), str(self.v_h.get() or "").strip()
        values = {
            "blend": os.path.basename(self.v_blend.get() or "—") or "—",
            "frames": " · ".join(segment_label(s) for s in segs if s.get("start") or s.get("end")) or "—",
            "count": "%d 帧" % len(frames),
            "engine": ("%s · 采样 %s" % (engine, samples)) if samples else engine,
            "device": self._short_choice(DEVICES, self.v_device.get()),
            "res": ("%s×%s @%s%%" % (w, h, self.v_pct.get() or "100")
                    if (w and h) else "随工程"),
            "format": fmt,
            "output": self._short_path(self.v_output.get() or "") or "—",
            "hidden": ("、".join(hidden[:2]) + ("　等 %d 个" % len(hidden)
                                              if len(hidden) > 2 else ""))
                      if hidden else "（无）",
            "retry": "%s · 断点 %s" % (
                "一直重试" if self.v_retry.get() == DEFAULT_RETRY
                else (self.v_retry.get() or "—"), bp_text),
        }
        for key, var in self._overview_tags.items():
            var.set(values.get(key, "—"))
        overview = getattr(self, "overview", None)
        if overview is not None:
            sel = self._selected_item() or {}
            name = os.path.basename((sel.get("config") or {}).get("blend")
                                    or self.v_blend.get() or "")
            overview.set_head(name or "未选工程",
                              sel.get("status") or queue_store.STATUS_PENDING)
        try:
            self.lbl_sum_overview.configure(
                text="%s · %d 帧" % (values["blend"], len(frames)))
            self.lbl_sum_project.configure(
                text=os.path.basename(self.v_blend.get() or "") or "未选工程")
            self.lbl_sum_frames.configure(text="%d 段 · %d 帧" % (len(segs), len(frames)))
            self.lbl_sum_render.configure(
                text="%s · %s · %sx%s" % (
                    self._choice_label(ENGINES, self.v_engine.get()),
                    self._choice_label(DEVICES, self.v_device.get()),
                    self.v_w.get() or "?", self.v_h.get() or "?"))
            self.lbl_sum_program.configure(
                text=os.path.basename(self.v_blender_ver.get()
                                      or self.v_blender.get() or "—"))
        except AttributeError:
            pass                                  # 初始化过程中摘要 Label 还没建全

    def _apply_queue_item(self, item):
        """把某一项的配置快照铺到右侧表单（切队列项 / 渲染下一项都走它）。"""
        if not item:
            return
        fm = FormModel.from_config(item.get("config") or {},
                                   item.get("blender_exe") or "")
        self._apply_form_model(fm)
        path = fm.blend
        cached = self._scene_info_cache.get(path)
        if cached:
            self._scene_info = cached
            self._inspected_path = path
            self._update_scene_combo(scene_names(cached), cached.get("scene") or "")
            self._rebuild_collection_ui(cached.get("collections") or [],
                                        fm.hidden_collections)
            self.btn_inspect.configure(text="重新读取工程配置", state="normal")
        else:
            self._scene_info = None
            self.btn_inspect.configure(text="读取工程配置", state="normal")
        self._refresh_state_label()
        self._sync_overview()

    def _snapshot_selected_item(self):
        """把当前表单写回选中项的快照（切项/勾选集合/读配置完成时调用）。"""
        item = self._selected_item()
        if not item:
            return
        self._collect_form()
        cfg, errors, _warns = self.form.to_config()
        if errors:
            return                       # 半填状态别写坏快照
        blender = self.v_blender.get().strip() or item.get("blender_exe") or ""
        if not self.demo and not self._plausible_blender(blender):
            blender = ""                 # 别把 python.exe 之类的假路径写回存档
        queue_store.update_config(item["id"], cfg, blender)
        self._sync_overview()

    def _on_queue_select(self, _event=None):
        if getattr(self, "_queue_refreshing", False):
            return
        sel = self.lst_queue.curselection()
        if not sel:
            return
        q = queue_store.load()
        if not q:
            return
        item = q["items"][sel[0]]
        if item.get("id") == self._selected_queue_id:
            # 点的就是当前项：配置不用重铺，但"点条目"本身仍是要看它，日志跟着展开
            panel = getattr(self, "log_panel", None)
            if panel is not None:
                panel.expand()
            return
        self._snapshot_selected_item()   # 先把旧项的修改存回去
        self._selected_queue_id = item.get("id")
        self._apply_queue_item(item)
        # 切到某一项＝要看这一项的情况，日志/进度面板跟着展开
        panel = getattr(self, "log_panel", None)
        if panel is not None:
            panel.expand()
        self.on_inspect()                 # 新工程自动读配置与集合状态

    def on_queue_reorder(self, src, dst):
        q = queue_store.load()
        if not q or not (0 <= src < len(q["items"])) or not (0 <= dst < len(q["items"])):
            return
        if src == dst:
            return
        item_id = q["items"][src]["id"]
        delta = 1 if dst > src else -1
        for _ in range(abs(dst - src)):
            if not queue_store.move(item_id, delta):
                break
        self._refresh_queue()

    def _on_queue_drag_start(self, event):
        self._drag_from = self.lst_queue.nearest(event.y)

    def _on_queue_drag_move(self, _event):
        return                            # 只在松手时真正移动，避免抖来抖去

    def _on_queue_drag_end(self, event):
        src = getattr(self, "_drag_from", None)
        self._drag_from = None
        if src is None:
            return
        dst = self.lst_queue.nearest(event.y)
        if src == dst:
            return
        q = queue_store.load()
        if not q or not (0 <= src < len(q["items"])) or not (0 <= dst < len(q["items"])):
            return
        item_id = q["items"][src]["id"]
        delta = 1 if dst > src else -1
        for _ in range(abs(dst - src)):
            if not queue_store.move(item_id, delta):
                break
        self._selected_queue_id = item_id
        self._refresh_queue()
        self._log("队列顺序已调整")

    def on_queue_add(self):
        """把当前表单加入队列；按工程路径去重，加入后自动读配置与集合状态。"""
        self._collect_form()
        cfg, errors, _warns = self.form.to_config()
        if errors:
            messagebox.showerror("配置有问题", "\n".join("· " + e for e in errors))
            return None
        blender = (self.form.blender or "").strip()
        if not blender or not os.path.exists(blender):
            messagebox.showerror("配置有问题", "先指定有效的 blender.exe")
            return None
        q = queue_store.load()
        want = os.path.normcase(os.path.abspath(cfg.blend))
        for it in (q or {}).get("items", []):
            old = os.path.normcase(os.path.abspath((it.get("config") or {}).get("blend") or ""))
            if old == want:
                self._selected_queue_id = it.get("id")
                queue_store.update_config(it["id"], cfg, blender)
                self._refresh_queue()
                self._apply_queue_item(it)
                self._log("工程已在队列中，已用当前配置更新它")
                self.on_inspect(force=True)
                return it
        _q, item = queue_store.add(cfg, blender)
        self._selected_queue_id = item["id"]
        self._log("已加入队列：%s（%d 帧）"
                  % (os.path.basename(cfg.blend), len(cfg.frames)))
        self._refresh_queue()
        self.on_inspect(force=True)      # 自动读取配置 + 集合状态，并刷新该项快照
        return item

    def on_queue_remove(self):
        sel = self.lst_queue.curselection()
        if not sel:
            return
        q = queue_store.load()
        if not q:
            return
        item = q["items"][sel[0]]
        if queue_store.remove(item.get("id")):
            self._log("已从队列移除：%s" % os.path.basename(item["config"]["blend"]))
        self._selected_queue_id = None
        self._refresh_queue()
        nxt = self._selected_item()
        if nxt:
            self._apply_queue_item(nxt)

    def on_queue_move(self, delta):
        sel = self.lst_queue.curselection()
        if not sel:
            return
        q = queue_store.load()
        if q and queue_store.move(q["items"][sel[0]].get("id"), delta):
            self._selected_queue_id = q["items"][sel[0]].get("id")
            self._refresh_queue()
