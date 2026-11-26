# -*- coding: utf-8 -*-
"""Blender 路径与 .blend 工程读取的控制器 mixin。

由 `brconsole/gui.py` 按变更原因抽出；方法体保持原样，便于逐步迁移到 view 契约。
"""

import os
import threading
from tkinter import filedialog, messagebox

from .. import locate
from ..guimodel.constants import SCENE_NEED_READ_LABEL, SCENE_PLACEHOLDERS
from ..guimodel.form import (FormModel, fields_from_detail,
                             format_from_label, format_label)
from ..guimodel.frames import new_segment
from ..guimodel.stateinfo import guess_state_path
from ..inspect import (read_blend_info, scene_detail, scene_names,
                       summarize as summarize_blend)


class InspectControllerMixin(object):
    @staticmethod
    def _plausible_blender(path):
        """路径看着像真 Blender 才信：文件名里得有 blender。

        demo 会把 sys.executable（python.exe）塞进队列项当 blender——那种项被
        选中时会把 v_blender 回填成 python.exe，之后读取/渲染全变成
        "python -b 工程.blend"，.blend 被当 Python 脚本执行（用户实际踩过）。
        """
        return "blender" in os.path.basename(str(path or "")).lower()

    def _autofill_blender(self):
        try:
            cands = locate.find_blender()
        except Exception:
            cands = []
        self._blender_candidates = list(cands)
        self._sync_blender_versions()
        if cands:
            self.view.set("blender", cands[0])
            if len(cands) > 1:
                self._log("检测到多个 Blender，使用 %s（其余：%s）"
                          % (cands[0], ", ".join(cands[1:3])))

    def _sync_blender_versions(self):
        """把 locate 的候选列表灌进版本下拉（显示父目录名，重名退回全路径）。"""
        self._blender_by_label = {}
        labels = []
        for p in self._blender_candidates:
            parent = os.path.basename(os.path.dirname(p))
            label = parent if parent else p
            if label in self._blender_by_label:      # 同名父目录 → 用全路径区分
                label = p
            self._blender_by_label[label] = p
            labels.append(label)
        self.view.configure("blender_ver", values=labels)
        self.view.set("blender_ver", labels[0] if labels else "")

    def on_blender_version(self, _event=None):
        """版本下拉选择 → 写入 Blender 路径（与手填等价，后续渲染用哪份一目了然）。"""
        path = self._blender_by_label.get(self.view.get("blender_ver"))
        if path and path != self.view.get("blender"):
            self.view.set("blender", path)
            self._log("Blender 改为：%s" % path)

    def _on_blender_var_changed(self, *args):
        """v_blender 变化时校准版本下拉（双向）：路径等于某候选 → 选中它；
        对不上（手填别的路径）→ 清空。下拉与输入框必须说的是同一件事，
        否则界面在说谎（下拉写着"Blender 5.2"，实际跑的是刚粘的另一个路径）。
        """
        target = os.path.normcase(self.view.get("blender"))
        match = next((l for l, p in self._blender_by_label.items()
                      if os.path.normcase(p) == target), None)
        if self.view.get("blender_ver") != (match or ""):
            self.view.set("blender_ver", match or "")

    def autofill_blender(self):
        old = self.view.get("blender")
        self._autofill_blender()
        if not self.view.get("blender"):
            messagebox.showwarning("没找到 Blender",
                                   "自动扫描没找到 blender.exe，请手动指定路径。")
        elif old and old != self.view.get("blender"):
            self._log("Blender 改为：%s" % self.view.get("blender"))

    def pick_blend(self):
        path = filedialog.askopenfilename(
            title="选择 .blend 工程",
            filetypes=[("Blender 工程", "*.blend"), ("所有文件", "*.*")])
        if path:
            self.view.set("blend", path)
            self.view.set("scene", SCENE_NEED_READ_LABEL)
            if not self.view.get("output"):
                self.view.set("output", FormModel.complete_output("", path))
            # 选工程即入队并自动读配置（HTML 定稿的交互），不再只填一个路径
            self.on_queue_add()

    def maybe_inspect(self):
        """手填/粘贴路径后失焦时也试着读一次（同一个工程不重复读）。"""
        p = self.view.get("blend").strip()
        if p and os.path.exists(p) and p != self._inspected_path:
            self.on_inspect()

    def on_inspect(self, force=False):
        """读当前工程的渲染配置。选择/添加队列项后会自动调用（同一个工程不重复读）。

        ⚠️ 异步竞态：读一次要起一次 Blender（几十秒），期间用户可能已切到别的工程。
        每次请求带一个 token，回主线程时校验 token + 路径 + 队列项，旧结果一律丢弃，
        否则会把 A 工程的配置写到 B 项上（HANDOFF 风险 2）。
        """
        if self._inspecting or self.prog.running:
            return                        # 渲染中别去抢 Blender
        blend = self.view.get("blend").strip()
        if not blend or not os.path.exists(blend):
            messagebox.showwarning("还没选工程", "先选一个 .blend 工程文件。")
            return
        if not force and blend == self._inspected_path and self._scene_info:
            return                        # 已经读过，切场景直接用内存里的多场景配置
        blender = self.view.get("blender").strip()
        # 名字里连 blender 都没有（如 demo 泄漏的 python.exe）→ 当没有配置过，重新探测
        if (not blender or not os.path.exists(blender)
                or (not self.demo and not self._plausible_blender(blender))):
            self._autofill_blender()
            blender = self.view.get("blender").strip()
        if not blender or not os.path.exists(blender):
            messagebox.showwarning("缺少 Blender 路径",
                                   "读取工程配置需要先指定 blender.exe。")
            return

        self._inspecting = True
        self._inspect_token += 1
        token = self._inspect_token
        self._inspect_path = blend
        self._inspect_item_id = self._selected_queue_id
        self._inspected_path = blend
        # 换工程了：旧工程的场景列表立刻作废，读完再填（否则会拿旧场景名去渲染）
        self._scene_info = None
        self._reset_scene_combo()
        self.view.set_inspect_enabled(False)
        self.view.set_text("inspect_note", "正在读取工程配置…", style="Muted.TLabel")
        self.log.add("读取工程配置：%s（一次会把工程里所有场景都读出来）"
                     % os.path.basename(blend))
        threading.Thread(target=self._inspect_worker,
                         args=(blender, blend, token), daemon=True).start()

    def _inspect_worker(self, blender, blend, token):
        try:
            info = read_blend_info(blender, blend, timeout=300)
        except Exception as e:
            info = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.q.put(("__inspect__", {"token": token, "info": info}))

    def _apply_inspect(self, payload):
        token = (payload or {}).get("token")
        info = (payload or {}).get("info")
        stale = (token != self._inspect_token
                 or (self._inspect_path or "") != self.view.get("blend").strip()
                 or (self._inspect_item_id or "") != (self._selected_queue_id or ""))
        if stale:
            self.log.add("工程配置读取结果已过期（期间切了工程），已丢弃")
            if token == self._inspect_token:
                self._inspecting = False
                self.view.set_inspect_enabled(True)
            return
        self._inspecting = False
        self.view.set_inspect_enabled(True)
        if not info or not info.get("ok"):
            msg = (info or {}).get("error", "未知错误")
            self.view.set_text("inspect_note", "读取失败（可手填下面的参数）",
                               style="Muted.TLabel")
            self.log.add("! 读取工程配置失败：%s" % msg.replace("\n", " "))
            self._scene_info = None
            self._reset_scene_combo()
            # 失败原因只在日志里：日志默认折叠着的话用户根本看不见，
            # 会以为"点了没反应/没修复"。失败时把面板展开。
            self.view.expand_log()
            return

        self._scene_info = info
        self._scene_info_cache[self.view.get("blend").strip()] = info
        self.view.configure("inspect", text="重新读取工程配置")
        names = scene_names(info)
        active = info.get("scene") or ""
        # 顶层字段就是「激活场景」那一份，直接拿来填表单
        self._fill_from_detail(info, active)

        res = info.get("resolution") or [0, 0]
        self.view.set_text(
            "inspect_note",
            "已读取：%s · 场景 %s · %sx%s · 采样 %s · 帧 %s-%s" % (
                info.get("engine"), active, res[0], res[1],
                "-" if info.get("samples") is None else info.get("samples"),
                info.get("frame_start"), info.get("frame_end")),
            style="OK.TLabel")
        self.log.add("已读取工程配置：" + summarize_blend(info))
        self._update_scene_combo(names, active)
        # 工程里本来就 hide_render 的集合 → hidden 列表 → 界面上不勾选
        collections = info.get("collections") or []
        project_hidden = [c.get("name") for c in collections
                          if isinstance(c, dict) and c.get("hidden")]
        self._rebuild_collection_ui(collections, project_hidden)
        self._report_scene_camera(info, active)
        # 自动读取完必须刷新该队列项的 cfg 与帧数，否则详情/后续渲染用旧值
        self._snapshot_selected_item()

    def _fill_from_detail(self, detail, name=""):
        """按一个场景的配置重填表单（引擎/采样/分辨率/输出路径、帧范围都是 per-scene 的）。

        帧范围**整段按工程重填**：读工程配置＝"以工程为准"，此时留着上一次的段
        会让界面显示一堆工程里根本没有的帧（上一版只覆盖第 1 段，用户看到的是
        骨架里残留的 2-3）。读完之后用户照样可以再加段。
        """
        fields = fields_from_detail(detail, self.view.get("blend").strip())
        if fields.get("start") or fields.get("end"):
            seg = new_segment(str(fields.get("start", "")),
                              str(fields.get("end", "")),
                              str(fields.get("step", "1") or "1"))
            self._set_segments([seg])
        for key in ("engine", "samples", "device", "width", "height",
                    "pct", "output"):
            if key in fields:
                self.view.set(key, fields[key])
        # 格式走显示文案（下拉里是"保持工程设置"这种中文，不是裸 identifier）
        if "file_format" in fields:
            self.view.set("format", format_label(fields["file_format"]))
        # 格式定下来之后位数下拉才有候选；工程里读到的位数交给它回填
        self._sync_depth_ui(format_from_label(self.view.get("format")),
                            fields.get("color_depth"))
        return fields

    def _rebuild_collection_ui(self, collections, hidden=()):
        """读工程配置后重建「集合显示」勾选清单。

        `collections` 是 inspect 出的 [{name, objects, hidden}, ...]；`hidden` 是
        **要隐藏**的名字（来自工程 hide_render 或队列项快照）。界面勾选态由
        `checks_from_hidden()` 反转得到 —— 不要在界面里改 `hidden` 的存储语义。
        """
        self._collection_objects = {}
        if not collections:
            self.view.set_text(
                "collection_status",
                "读取工程配置后可勾选；勾选＝渲染时显示，取消＝渲染时隐藏",
                style="Muted.TLabel")
            self.hidden_collections = []
            return
        self.view.set_text(
            "collection_status",
            "勾选＝渲染时显示；取消勾选＝渲染时隐藏（只内存生效，不改 .blend）",
            style="Muted.TLabel")
        names = [(item.get("name") if isinstance(item, dict) else str(item))
                 for item in collections]
        for item in collections:
            name = item.get("name") if isinstance(item, dict) else str(item)
            self._collection_objects[name] = (item.get("objects")
                                              if isinstance(item, dict) else None)
        self.hidden_collections = list(hidden)
        self.view.rebuild_collections(collections, hidden,
                                      on_toggle=self._on_collection_toggle)
        self._sync_overview()

    def _on_collection_toggle(self):
        """界面勾选变了 → 重算 hidden 列表（存储仍是隐藏列表）+ 刷新当前项快照。"""
        self.hidden_collections = self.view.collection_hidden()
        self._snapshot_selected_item()
        if self.hidden_collections:
            self.log.add("渲染时隐藏：%s（已保存，点开始渲染时生效）"
                         % "、".join(self.hidden_collections))

    def _update_scene_combo(self, names, active):
        """场景下拉：只有一个场景时只显示名字，多个时可切换。"""
        if not names:
            self._reset_scene_combo()
            return
        self.view.configure("scene", values=names, state="readonly")
        self.view.set("scene", active if active in names else names[0])
        if len(names) == 1:
            self.view.set_text("scene_note", "工程里只有这一个场景",
                               style="Muted.TLabel")
        else:
            self.view.set_text(
                "scene_note",
                "工程有 %d 个场景，可切换（切换后参数按该场景重填）" % len(names),
                style="OK.TLabel")
            self.log.add("注意：工程里有 %d 个场景：%s。当前用「%s」（Blender 里激活的那个），"
                         "可在「场景」里切换。" % (len(names), "、".join(names), active))

    def _reset_scene_combo(self):
        self.view.configure("scene", values=[SCENE_NEED_READ_LABEL], state="disabled")
        self.view.set("scene", SCENE_NEED_READ_LABEL)
        self.view.set_text("scene_note",
                           "读取工程配置后可用；工程里只有一个场景时不用管它",
                           style="Muted.TLabel")

    def _report_scene_camera(self, detail, name):
        """没有相机的场景 Blender 会直接拒绝渲染 —— 早点说，别等按下开始才报错。"""
        if detail and not detail.get("camera"):
            self.log.add("! 场景「%s」没有设置相机，渲染这个场景会失败（在 Blender 里给它指定相机）"
                         % name)

    def on_scene_change(self, _event=None):
        """切换场景：按该场景的配置重填表单（不用再起一次 Blender）。"""
        name = self.view.get("scene")
        if name in SCENE_PLACEHOLDERS:
            return
        info = self._scene_info or {}
        if os.path.abspath(info.get("blend") or "") != os.path.abspath(self.view.get("blend").strip()):
            self.log.add("! 换过工程了，请点「读取工程配置」重新读一次再切换场景")
            return
        detail = scene_detail(info, name)
        if detail is None:
            self.log.add("! 读到的配置里没有场景「%s」，请点「读取工程配置」重新读一次" % name)
            return

        self._fill_from_detail(detail, name)
        res = detail.get("resolution") or [0, 0]
        self.view.set_text(
            "inspect_note",
            "场景 %s：%s · %sx%s · 采样 %s · 帧 %s-%s" % (
                name, detail.get("engine"), res[0], res[1],
                "-" if detail.get("samples") is None else detail.get("samples"),
                detail.get("frame_start"), detail.get("frame_end")),
            style="OK.TLabel")
        self.log.add("已切换到场景 %s（引擎 %s · %sx%s · 帧 %s-%s）"
                     % (name, detail.get("engine"), res[0], res[1],
                        detail.get("frame_start"), detail.get("frame_end")))
        self._report_scene_camera(detail, name)

    def pick_output(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.view.set("output", os.path.join(d, "frame_####"))

    def open_output_dir(self):
        if not self.view.get("output").strip():
            messagebox.showinfo("还没填输出模板", "先填输出模板（或选个 .blend 自动补全）。",
                                parent=self.root)
            return
        d = os.path.dirname(os.path.abspath(self.view.get("output")))
        if os.path.isdir(d):
            try:
                os.startfile(d)                 # Windows
            except Exception as e:
                self._log("打不开目录：%r" % (e,))
        else:
            messagebox.showinfo("目录还不存在", "输出目录还没创建：%s" % d)

    def clear_state(self):
        if self.prog.running:
            messagebox.showinfo("渲染进行中",
                                "正在渲染，断点由任务自己维护。\n要清空请先停止任务。",
                                parent=self.root)
            return
        p = guess_state_path(self.view.get("output"))
        if os.path.exists(p):
            try:
                os.remove(p)
                self._log("已清空断点：%s" % p)
            except OSError as e:
                messagebox.showerror("删除失败", str(e))
        else:
            self._log("没有断点文件（%s）" % p)
        self._refresh_state_label()
