# -*- coding: utf-8 -*-
"""表单、帧段、格式与重试策略的控制器 mixin。

由 `brconsole/gui.py` 按变更原因抽出；方法体保持原样，便于逐步迁移到 view 契约。
"""

from tkinter import messagebox

from .. import autostart, taskstore
from ..guimodel.constants import DEPTH_KEEP_LABEL, RETRY_CHOICES
from ..guimodel.form import (coerce_depth, depth_choices, depth_value,
                             format_ext_for, format_from_label, format_label,
                             retarget_extension, retry_note)
from ..guimodel.frames import frames_preview, normalise_segment


class FormControllerMixin(object):
    def _add_segment_row(self, seg, focus=False):
        seg = normalise_segment(seg)
        return self.view.add_segment(seg.get("start", ""), seg.get("end", ""),
                                     seg.get("step", "1"), focus=focus)

    def _set_segments(self, segments):
        self.view.set_segments(segments)

    def _sync_segment_ui(self):
        self.view.refresh_segments()

    def _read_segments(self):
        return self.view.read_segments()

    def _refresh_frame_preview(self):
        segments = self._read_segments()
        frames, text, ok = frames_preview(segments)
        self._frame_preview = frames
        self.view.set_text("frames_preview", text,
                           style="OK.TLabel" if ok else "Warn.TLabel")
        self.view.refresh_segments()

    def _on_seg_box_resize(self, _event):
        return

    def on_add_segment(self):
        self.view.add_segment("", "", "1", focus=True)
        self._log("新增第 %d 段帧范围 —— 填「起」（只渲一帧就只填起帧，"
                  "要区间就把「止」也填上）" % self.view.segment_count())

    def on_remove_segment(self, handle):
        if self.view.segment_count() <= 1:
            return
        self.view.remove_segment(handle)
        self._log("已移除 1 段帧范围，现在 %d 段" % self.view.segment_count())

    def _sync_autostart_ui(self):
        """把勾选态对齐到**注册表的真实情况**（而不是程序自己记的状态）。"""
        if not autostart.supported():
            self.view.set("autostart", False)
            self.view.set_disabled("autostart", True)
            self.view.set_text("autostart_note", "（当前系统不支持开机自启）")
            return
        cmd = autostart.registered_command()
        self.view.set("autostart", bool(cmd))
        if not cmd:
            self.view.set_text("autostart_note", "")
        elif autostart.is_current():
            self.view.set_text("autostart_note", "已登记：%s" % cmd)
        else:
            # 换过目录/换过解释器：开机拉起的是**另一个**程序，必须让用户看见
            self.view.set_text(
                "autostart_note",
                "⚠ 登记的是别的位置：%s（重新勾一次即刷新）" % cmd)

    def on_toggle_autostart(self):
        want = bool(self.view.get("autostart"))
        ok, msg = autostart.sync(want)
        if not ok:
            # 注册表没改成功 → 勾选必须回滚，否则界面在撒谎
            self.view.set("autostart", not want)
            messagebox.showerror("开机自启设置失败", msg, parent=self.root)
            return
        self._log(msg)
        self._sync_autostart_ui()

    def on_toggle_auto_resume(self):
        """「启动时自动续跑未完成任务」——不只是个偏好，还当场改存档里的标记。

        为什么要当场改：用户取消勾选时的意思就是"别自己跑"。等到下次点开始才生效的话，
        这段时间里机器一崩，开机还是会自动跑起来 —— 与他的意思相反。
        """
        want = bool(self.view.get("auto_resume"))
        # 只写 taskstore：它先记偏好（没待办时也生效），set_current() 再据此写 pending
        if taskstore.load() is None:
            # 别悄悄吞掉 —— 勾了没反应，用户会以为坏了
            self._log("当前没有待办任务：这个开关会在下次点「开始渲染」时写入存档")
            return
        if taskstore.set_autoresume(
                want, "" if want else "你在界面上取消了「启动时自动续跑未完成任务」"):
            self._log("已%s开机自动续跑未完成任务（断点不受影响）"
                      % ("开启" if want else "取消"))

    def _sync_resume_ui(self):
        """勾选态对齐到**用户偏好**（没待办任务时也能记住上次的选择）。"""
        rec = taskstore.load()
        self.view.set(
            "auto_resume",
            bool(taskstore.autoresume_preference() if rec is None
                 else rec.get("autoresume", True)))

    def _depth_value(self):
        """位数下拉的当前实际取值（"随工程" → 空串）。"""
        return depth_value(self.view.get("depth"))

    def _sync_depth_ui(self, fmt, depth=None):
        """按格式刷新位数下拉的候选与取值，返回 `(实际位数, 是否被改过)`。

        候选来自 `guimodel.depth_choices()`（同一张实测表），**不在这里再写一份**
        "哪个格式认几位"—— 两张表一定会漂（AGENTS 坑 31/44）。
        格式是「保持工程设置」时控件禁用：没有可选的位数，也不该让人以为能单独改。
        """
        fmt = str(fmt or "").strip().upper()
        old = self._depth_value() if depth is None else str(depth or "").strip()
        values = depth_choices(fmt)
        self.view.configure("depth", values=values)
        if not fmt:
            self.view.configure("depth", state="disabled")
            self.view.set("depth", DEPTH_KEEP_LABEL)
            return "", bool(old)
        self.view.configure("depth", state="readonly")
        d, changed = coerce_depth(fmt, old)
        self.view.set("depth", d)
        return d, changed

    def _on_format_changed(self, _event=None):
        """切格式：位数收敛到白名单 + 输出模板的扩展名跟着改。"""
        fmt = format_from_label(self.view.get("format"))
        depth, changed = self._sync_depth_ui(fmt)
        tpl = self.view.get("output").strip()
        if tpl:
            new_tpl = retarget_extension(tpl, fmt)
            if new_tpl != tpl:
                self.view.set("output", new_tpl)
                self.log.add("输出模板的扩展名跟着格式改成 .%s"
                             "（不改的话会写出一个扩展名名不副实的文件）"
                             % format_ext_for(fmt))
        if changed:
            if fmt:
                self.log.add("位数已改成 %s 位：%s 只支持 %s"
                             % (depth, fmt, " / ".join(depth_choices(fmt))))
            else:
                self.log.add("格式回到「保持工程设置」，位数也一并交还给工程（不覆盖）")

    def _sync_retry_note(self):
        """切下拉时**只改旁边那句说明**，不动别的控件。

        三个重试参数（重启次数 / 单帧尝试 / 无进展兜底）已经由选中的次数在
        `guimodel.retry_plan()` 里一次算出，所以没有"切选项时要同步另一个框"这回事 ——
        那正是原来那套（下拉 + 两个数字框）最招人烦的地方。
        """
        self.view.set_text("retry_note", retry_note(self.view.get("retry")))

    def _apply_form_model(self, fm):
        """把 `FormModel` 铺到控件上（开机续跑回填用）。"""
        self.form = fm
        self.view.set("blend", fm.blend)
        # 存档里的 blender 可能是 demo 泄漏的 python.exe —— 不是真 Blender 就别铺
        if self.demo or not fm.blender or self._plausible_blender(fm.blender):
            self.view.set("blender", fm.blender)
        self.view.set("output", fm.output)
        self._set_segments(fm.segments)
        self.view.set("engine", fm.engine)
        self.view.set("samples", fm.samples)
        self.view.set("device", fm.device)
        self.view.set("width", fm.width)
        self.view.set("height", fm.height)
        self.view.set("pct", fm.pct)
        self.view.set("format", format_label(fm.file_format))
        self._sync_depth_ui(format_from_label(self.view.get("format")), fm.color_depth)
        self.view.set("scene", fm.scene)
        # 隐藏集合：有集合清单时用勾选框回填；清单还没读出来时先把名字存着，
        # 等读工程配置后 _rebuild_collection_ui 会用它当 selected 参数
        self.hidden_collections = list(fm.hidden_collections)
        self._rebuild_collection_ui(
            [{"name": n, "objects": self._collection_objects.get(n)}
             for n in self._collection_objects] or [], self.hidden_collections)
        self.view.set("resume", fm.resume)
        # 存档里的次数已经由 `FormModel.from_config` 变成下拉文案，这里贴上去 + 刷新说明。
        # 预设里没有的次数（老存档里的"7 次"）要临时补进候选，否则 readonly 的下拉
        # 显示不出这个值、看起来像"回填丢了"。
        label = fm.retry
        values = list(RETRY_CHOICES)
        if label not in values:
            values.append(label)
        self.view.configure("retry", values=values)
        self.view.set("retry", label)
        self._sync_retry_note()

    def _collect_form(self):
        self.form.blend = self.view.get("blend")
        self.form.scene = self.view.get("scene")
        self.form.blender = self.view.get("blender")
        self.form.output = self.view.get("output")
        self.form.segments = self._read_segments()
        self.form.engine = self.view.get("engine")
        self.form.samples = self.view.get("samples")
        self.form.device = self.view.get("device")
        self.form.width = self.view.get("width")
        self.form.height = self.view.get("height")
        self.form.pct = self.view.get("pct")
        self.form.file_format = format_from_label(self.view.get("format"))
        self.form.color_depth = self._depth_value()
        self.form.hidden_collections = list(self.hidden_collections)
        self.form.resume = self.view.get("resume")
        self.form.retry = self.view.get("retry")
