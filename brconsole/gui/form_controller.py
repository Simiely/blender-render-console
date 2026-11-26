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
from .common import fit_combobox


class FormControllerMixin(object):
    def _add_segment_row(self, seg, focus=False):
        seg = normalise_segment(seg)
        handle = self.frames_module.add_segment(
            seg.get("start", ""), seg.get("end", ""), seg.get("step", "1"),
            focus=focus)
        self._seg_rows = list(self.frames_module.rows)
        return handle

    def _set_segments(self, segments):
        self.frames_module.set_segments(segments)
        self._seg_rows = list(self.frames_module.rows)

    def _sync_segment_ui(self):
        self.frames_module.refresh_counts()

    def _read_segments(self):
        return self.frames_module.read_segments()

    def _refresh_frame_preview(self):
        segments = self._read_segments()
        frames, text, ok = frames_preview(segments)
        self._frame_preview = frames
        self.lbl_frames.configure(text=text,
                                  style="OK.TLabel" if ok else "Warn.TLabel")
        self.frames_module.refresh_counts()

    def _on_seg_box_resize(self, _event):
        return

    def on_add_segment(self):
        self.frames_module.add_segment("", "", "1", focus=True)
        self._seg_rows = list(self.frames_module.rows)
        self._log("新增第 %d 段帧范围 —— 填「起」（只渲一帧就只填起帧，"
                  "要区间就把「止」也填上）" % len(self.frames_module.rows))

    def on_remove_segment(self, handle):
        if len(self.frames_module.rows) <= 1:
            return
        self.frames_module.remove_handle(handle)
        self._seg_rows = list(self.frames_module.rows)
        self._log("已移除 1 段帧范围，现在 %d 段"
                  % len(self.frames_module.rows))

    def _sync_autostart_ui(self):
        """把勾选态对齐到**注册表的真实情况**（而不是程序自己记的状态）。"""
        if not autostart.supported():
            self.v_autostart.set(False)
            self.chk_autostart.configure(state="disabled")
            self.lbl_autostart.configure(text="（当前系统不支持开机自启）")
            return
        cmd = autostart.registered_command()
        self.v_autostart.set(bool(cmd))
        if not cmd:
            self.lbl_autostart.configure(text="")
        elif autostart.is_current():
            self.lbl_autostart.configure(text="已登记：%s" % cmd)
        else:
            # 换过目录/换过解释器：开机拉起的是**另一个**程序，必须让用户看见
            self.lbl_autostart.configure(
                text="⚠ 登记的是别的位置：%s（重新勾一次即刷新）" % cmd)

    def on_toggle_autostart(self):
        want = bool(self.v_autostart.get())
        ok, msg = autostart.sync(want)
        if not ok:
            # 注册表没改成功 → 勾选必须回滚，否则界面在撒谎
            self.v_autostart.set(not want)
            messagebox.showerror("开机自启设置失败", msg, parent=self.root)
            return
        self._log(msg)
        self._sync_autostart_ui()

    def on_toggle_auto_resume(self):
        """「启动时自动续跑未完成任务」——不只是个偏好，还当场改存档里的标记。

        为什么要当场改：用户取消勾选时的意思就是"别自己跑"。等到下次点开始才生效的话，
        这段时间里机器一崩，开机还是会自动跑起来 —— 与他的意思相反。
        """
        want = bool(self.v_auto_resume.get())
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
        self.v_auto_resume.set(
            bool(taskstore.autoresume_preference() if rec is None
                 else rec.get("autoresume", True)))

    def _depth_value(self):
        """位数下拉的当前实际取值（"随工程" → 空串）。"""
        return depth_value(self.v_depth.get())

    def _sync_depth_ui(self, fmt, depth=None):
        """按格式刷新位数下拉的候选与取值，返回 `(实际位数, 是否被改过)`。

        候选来自 `guimodel.depth_choices()`（同一张实测表），**不在这里再写一份**
        "哪个格式认几位"—— 两张表一定会漂（AGENTS 坑 31/44）。
        格式是「保持工程设置」时控件禁用：没有可选的位数，也不该让人以为能单独改。
        """
        fmt = str(fmt or "").strip().upper()
        old = self._depth_value() if depth is None else str(depth or "").strip()
        values = depth_choices(fmt)
        self.cmb_depth.configure(values=values)
        fit_combobox(self.cmb_depth, values=values)    # 候选变长（如 16 位）时别被截尾
        if not fmt:
            self.cmb_depth.configure(state="disabled")
            self.v_depth.set(DEPTH_KEEP_LABEL)
            return "", bool(old)
        self.cmb_depth.configure(state="readonly")
        d, changed = coerce_depth(fmt, old)
        self.v_depth.set(d)
        return d, changed

    def _on_format_changed(self, _event=None):
        """切格式：位数收敛到白名单 + 输出模板的扩展名跟着改。"""
        fmt = format_from_label(self.v_format.get())
        depth, changed = self._sync_depth_ui(fmt)
        tpl = self.v_output.get().strip()
        if tpl:
            new_tpl = retarget_extension(tpl, fmt)
            if new_tpl != tpl:
                self.v_output.set(new_tpl)
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
        self.lbl_retry_note.configure(text=retry_note(self.v_retry.get()))

    def _apply_form_model(self, fm):
        """把 `FormModel` 铺到控件上（开机续跑回填用）。"""
        self.form = fm
        self.v_blend.set(fm.blend)
        # 存档里的 blender 可能是 demo 泄漏的 python.exe —— 不是真 Blender 就别铺
        if self.demo or not fm.blender or self._plausible_blender(fm.blender):
            self.v_blender.set(fm.blender)
        self.v_output.set(fm.output)
        self._set_segments(fm.segments)
        self.v_engine.set(fm.engine)
        self.v_samples.set(fm.samples)
        self.v_device.set(fm.device)
        self.v_w.set(fm.width)
        self.v_h.set(fm.height)
        self.v_pct.set(fm.pct)
        self.v_format.set(format_label(fm.file_format))
        self._sync_depth_ui(format_from_label(self.v_format.get()), fm.color_depth)
        self.v_scene.set(fm.scene)
        # 隐藏集合：有集合清单时用勾选框回填；清单还没读出来时先把名字存着，
        # 等读工程配置后 _rebuild_collection_ui 会用它当 selected 参数
        self.hidden_collections = list(fm.hidden_collections)
        self._rebuild_collection_ui(
            [{"name": n, "objects": self._collection_objects.get(n)}
             for n in self._collection_objects] or [], self.hidden_collections)
        self.v_resume.set(fm.resume)
        # 存档里的次数已经由 `FormModel.from_config` 变成下拉文案，这里贴上去 + 刷新说明。
        # 预设里没有的次数（老存档里的"7 次"）要临时补进候选，否则 readonly 的下拉
        # 显示不出这个值、看起来像"回填丢了"。
        label = fm.retry
        values = list(RETRY_CHOICES)
        if label not in values:
            values.append(label)
        self.cmb_retry.configure(values=values)
        fit_combobox(self.cmb_retry, values)
        self.v_retry.set(label)
        self._sync_retry_note()

    def _collect_form(self):
        self.form.blend = self.v_blend.get()
        self.form.scene = self.v_scene.get()
        self.form.blender = self.v_blender.get()
        self.form.output = self.v_output.get()
        self.form.segments = self._read_segments()
        self.form.engine = self.v_engine.get()
        self.form.samples = self.v_samples.get()
        self.form.device = self.v_device.get()
        self.form.width = self.v_w.get()
        self.form.height = self.v_h.get()
        self.form.pct = self.v_pct.get()
        self.form.file_format = format_from_label(self.v_format.get())
        self.form.color_depth = self._depth_value()
        self.form.hidden_collections = list(self.hidden_collections)
        self.form.resume = self.v_resume.get()
        self.form.retry = self.v_retry.get()
