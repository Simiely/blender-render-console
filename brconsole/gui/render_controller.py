# -*- coding: utf-8 -*-
"""启动、停止、队列顺序渲染与关机收尾的控制器 mixin。

由 `brconsole/gui.py` 按变更原因抽出；方法体保持原样，便于逐步迁移到 view 契约。
"""

import os
import threading
from tkinter import messagebox

from .. import diskspace, power, queue_store, taskstore
from ..core import JobConfig, RenderJob
from ..guimodel.form import FormModel, scene_to_config
from ..inspect import scene_names
from .common import fit_combobox  # noqa: F401


class RenderControllerMixin(object):
    def _run_next_queue_item(self):
        """取下一项设为当前并启动渲染；队列跑完则收尾。"""
        item = queue_store.next_pending()
        if item is None:
            self._finish_queue()
            return
        cfg = JobConfig.from_dict(item["config"])
        blender = (item.get("blender_exe") or "").strip()
        bad = ""
        if (not blender or not os.path.exists(blender)
                or (not self.demo and not self._plausible_blender(blender))):
            bad = "blender.exe 不存在或无效：%s" % (blender or "未指定")
        elif not cfg.blend or not os.path.exists(cfg.blend):
            bad = "工程文件不存在：%s" % cfg.blend
        elif not cfg.frames:
            bad = "没有可渲染的帧"
        else:
            level, why = diskspace.check(cfg)
            if level == "block":
                bad = "磁盘空间不足：%s" % why.replace("\n", " ")
            elif level == "warn":
                for line in (why or "").splitlines():
                    self._log("! %s" % line)
        if bad:
            # 一项坏掉不该拖住整个队列：标失败、说清楚，然后接着下一项
            self.log.add("! 队列项跳过：%s —— %s"
                         % (os.path.basename(item["config"]["blend"]), bad))
            queue_store.set_status(item["id"], queue_store.STATUS_FAILED)
            self._refresh_queue()
            self.root.after(100, self._run_next_queue_item)
            return
        queue_store.set_current(item["id"])   # 顺带写 pending.json，供开机续跑认领
        self._current_queue_item = item
        self._selected_queue_id = item["id"]
        self._apply_queue_item(item)
        self._queue_index += 1
        self._start_cfg(cfg, blender, segments=len(cfg.frames))
        self._log("队列：开始第 %d/%d 项 —— %s（%d 帧）" % (
            self._queue_index, self._queue_total, os.path.basename(cfg.blend),
            len(cfg.frames)))
        self._refresh_queue()

    def _finish_queue(self):
        self._queue_mode = False
        self._log("队列全部完成（%d 项）。" % self._queue_index)
        self._refresh_queue()
        # 关机/睡眠只在**整个队列**跑完后触发，不在中间项触发
        if self._last_queue_event:
            kind, ev = self._last_queue_event
            self._maybe_aftermath(kind, ev)

    def try_autostart_resume(self):
        """开机自启拉起时的入口：认领未完成任务。

        ⚠️ 整段包一层 try —— 这是**无人值守**路径（由 `root.after` 拉起），
        而打包成窗口版后 stderr 无处可去：一旦这里抛异常，用户看到的就是
        "设了开机自启、开完机什么都没发生"，一个字的线索都没有。
        出错时至少要留下日志，并把存档**原样留着**（下次还能认领）。
        """
        try:
            self._adopt_pending()
        except Exception as e:
            self._log("! 认领未完成任务时出错：%s: %s" % (type(e).__name__, e))
            self._log("  待办存档保留在 %s，修好后重开即可继续" % taskstore.pending_path())

    def _adopt_pending(self):
        rec = taskstore.load()
        if rec is None:
            self._log("开机自启：没有未完成任务，正常启动")
            return
        action, why = taskstore.resume_decision(rec)
        if action == "none":
            return
        if action == "done":
            self._log("上次的任务已经渲完（%s），清理待办存档" % taskstore.describe(rec))
            taskstore.clear()
            return
        if action == "drop":
            # ⚠️ 不删档：工程/blender 挪了位置都是可恢复的，删了就找不回这次任务
            self._log("! 有待办任务但暂时跑不了：%s" % why)
            self._log("  存档保留在 %s，恢复后重开即可继续" % taskstore.pending_path())
            return

        self._apply_form_model(FormModel.from_config(rec.get("config") or {},
                                                     rec.get("blender_exe") or ""))
        if action == "prompt":
            self._log("检测到未完成任务：%s" % taskstore.describe(rec))
            # 原因本身可能带括号（"上次运行没能跑完（有帧一直失败）"），所以别再套一层括号
            self._log("  不自动跑 —— %s；确认参数后点「开始渲染」接着跑"
                      % (why or "自动续跑已关闭"))
            return
        self._log("检测到未完成任务，自动续跑：%s" % taskstore.describe(rec))
        self.root.after(300, self.on_start)

    def on_start(self):
        """「开始渲染」= 按队列顺序跑完整个队列（已删除单独的「跑队列」入口）。

        队列为空时把当前表单当成唯一一项入队，保留"配好参数直接点开始"的用法；
        非空时严格按队列里项的**存储快照**跑，不拿右侧当前表单覆盖别人。
        """
        if self.prog.running or self._queue_mode:
            messagebox.showinfo("渲染进行中", "当前有任务在跑，等它结束再开始。")
            return
        q = queue_store.load()
        if not q or not q.get("items"):
            self._collect_form()
            cfg, errors, warns = self.form.to_config()
            for w in warns:
                self._log("! %s" % w)
            if errors:
                messagebox.showerror("配置有问题", "\n".join("· " + e for e in errors))
                return
            bad = self._scene_conflict()
            if bad:
                messagebox.showerror("配置有问题", bad)
                return
            blender = (self.form.blender or "").strip()
            if not blender or not os.path.exists(blender):
                messagebox.showerror("配置有问题", "blender.exe 不存在：%s" % blender)
                return
            # 空间预检要在入队前：拦下来的任务不该留下待办存档
            level, why = diskspace.check(cfg)
            if level == "block":
                messagebox.showerror("磁盘空间不足，先别开始", why)
                return
            for line in (why or "").splitlines():
                self._log("! %s" % line)
            _q, item = queue_store.add(cfg, blender)
            self._selected_queue_id = item["id"]
            q = queue_store.load()

        # 上一轮已经全部跑完 → 重置状态重新跑，否则「开始渲染」看起来像没反应
        if not any(it.get("status") != queue_store.STATUS_DONE for it in q["items"]):
            for it in q["items"]:
                queue_store.set_status(it["id"], queue_store.STATUS_PENDING)
            q = queue_store.load()
            self._log("上一轮队列已全部完成，重置状态后重新按顺序渲染")
        # 校验全部通过后才撤倒计时：否则配置错了却把用户设好的关机一并撤掉
        self._cancel_pending_countdown()
        self._queue_mode = True
        self._queue_total = len(q["items"])
        self._queue_index = 0
        self._log("开始渲染：按队列顺序跑 %d 项" % self._queue_total)
        self._run_next_queue_item()

    def _start_cfg(self, cfg, blender, segments=None):
        """启动队列中的一项；pending.json 已由 `set_current()` 写好，别再覆盖。

        逐项校验（blender / 工程 / 帧 / 磁盘空间）在 `_run_next_queue_item()` 做，
        坏项标失败后继续下一项，不让一项拖住整个队列。
        """
        # 防睡眠守卫在这里注入（组合根职责）：CLI 与 GUI 都长跑，谁构造谁注入
        self.job = RenderJob(cfg, blender, cmd_factory=self.cmd_factory,
                             power_guard=(power.keep_awake, power.allow_sleep))
        self.stopping = False
        self.view.set_running(True)
        # 模型必须在这里就复位：`job_start` 事件要等后台线程跑起来才到（中间还有
        # 建目录、读/写断点这些磁盘 IO），而 `_refresh` 每 80ms 就用模型刷一次界面 ——
        # 不复位的话，进度条会先跳回上一轮的百分比再归零，状态栏也会闪一下旧文案。
        self.prog.reset()
        self.view.set_progress(0)
        self._refresh()
        # 这次从哪个断点开始，开始前就说清楚（否则"看着像从零开始"）
        self._refresh_state_label()
        self.view.set_clear_state_enabled(False)
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        # 把"哪几段、共几帧"写进日志：过夜挂机时回头查日志，得能一眼看出这次渲的是什么
        nseg = segments if segments is not None else len(self.form.segments)
        self._log("开始渲染：%s%s（%d 段，%d 帧 —— %s）"
                  % (cfg.blend, "（场景 %s）" % cfg.scene if cfg.scene else "",
                     nseg, len(cfg.frames),
                     self.view.text("frames_preview")))
        # 首帧准备提示前置：大工程读 .blend + 加载贴图 + 上传 GPU 实测可达 110s，
        # 这段时间 Blender 没有任何 stdout，用户最容易误判卡死而点停止（本项目真实事故）。
        self._log("  提示：首帧准备（读工程/加载贴图/上传场景）大工程可能要 1～3 分钟，"
                  "期间无进度输出属正常，请先耐心等待再决定是否停止")

    def _scene_conflict(self):
        """选了场景、但该工程里没这个名字 → 拦下来。

        为什么要在界面拦：命令行 `-S 不存在的场景` 时 Blender **只打一行英文提示就
        照常渲染默认场景、退出码还是 0**（实测 5.2.2），不拦就会静默渲出一整套错图。
        """
        scene = scene_to_config(self.view.get("scene"))
        info = self._scene_info
        if not scene or not info:
            return ""
        if os.path.abspath(info.get("blend") or "") != os.path.abspath(self.view.get("blend").strip()):
            return ""                      # 配置是上一个工程读的，别拿它下判断
        names = scene_names(info)
        if names and scene not in names:
            return ("工程里没有场景「%s」。\n\n现有场景：%s\n\n"
                    "点「读取工程配置」刷新一遍再选。" % (scene, "、".join(names)))
        return ""

    def _worker(self):
        """后台线程：只负责跑任务，事件一律入队。"""
        try:
            self.job.run(on_event=lambda kind, ev: self.q.put((kind, ev)))
        except Exception as e:                   # 兜底：core 本身不抛，但别把线程打死
            self.q.put(("job_error", {"error": "%s: %s" % (type(e).__name__, e)}))

    def on_stop(self):
        if self.job is None or self.stopping:
            return
        self.stopping = True
        self.job.cancel()
        self.view.configure("stop", state="disabled")
        self._log("正在停止…（已完成的部分已保存，下次可从断点继续）")
        # 主动停止 = "我知道，别自己跑"：撤掉自动续跑标记，但断点文件原样留着。
        # 改标记而不是删存档 —— 删了断点就成了"没跑过"，用户还得从头来。
        if taskstore.set_autoresume(False, "你点了「停止」"):
            self._log("已取消开机自动续跑（断点保留，想继续随时点「开始渲染」）")

    def on_close(self):
        if self.job is not None and self.prog.running:
            if not messagebox.askokcancel("任务还在跑", "停止并退出？进度已保存。",
                                          parent=self.root):
                return
            self.job.cancel()
            # 关窗退出等同于「主动停止」：撤掉自动续跑，别下次开机偷跑
            taskstore.set_autoresume(False, "你在渲染过程中关掉了窗口")
            if self.worker and self.worker.is_alive():
                self.worker.join(timeout=5)
        self.root.destroy()
