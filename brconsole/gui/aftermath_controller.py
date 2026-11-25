# -*- coding: utf-8 -*-
"""任务结束后的倒计时与关机/睡眠动作。

由 `brconsole/gui/app.py` 按职责抽出；方法体保持原样。
"""

from .common import *  # noqa: F401,F403
from .common import _stamp  # noqa: F401


class AftermathControllerMixin(object):
    def _maybe_aftermath(self, kind, ev):
        """渲完 → 按下拉选择执行关机/睡眠（60 秒倒计时，可取消）。

        只有「全部渲完」触发（决策在 guimodel.aftermath_plan：取消/失败不动作 ——
        用户在场或需要人来看原因）；demo 自检绝不触发。
        """
        if kind != "job_done" or self.demo:
            return
        plan = aftermath_plan(AFTERMATH_BY_LABEL.get(self.v_aftermath.get()),
                              bool(ev.get("ok")), bool(ev.get("cancelled")))
        if plan == AFTERMATH_SHUTDOWN:
            self._countdown("关机", power.SHUTDOWN_COUNTDOWN_SECONDS,
                            lambda: power.shutdown_after(0))
        elif plan == AFTERMATH_SLEEP:
            self._countdown("睡眠", power.SHUTDOWN_COUNTDOWN_SECONDS, power.sleep_now)

    def _countdown(self, action_name, seconds, fire):
        """倒计时弹窗：到 0 执行 fire()；点「取消」或关窗 = 不执行。

        刻意**不在弹窗前就排系统级关机**（`shutdown /t`）：万一本程序在这 60 秒里
        崩了，OS 倒计时照样执行 —— "崩了反而把机器关了"是错误方向；
        由本程序在 0 秒时才发动作，崩了就停在原地（安全侧）。

        弹窗句柄记在 `_pending_countdown`：用户在这 60 秒里点「开始渲染」开新任务时，
        `on_start` 会撤掉它 —— 否则机器会把刚开的新渲染关掉。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("任务完成 · 即将%s" % action_name)
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        ttk.Label(dlg, text="全部渲染完成，%d 秒后自动%s。" % (seconds, action_name),
                  padding=(18, 14, 18, 2)).pack()
        left = {"n": seconds}
        lbl = ttk.Label(dlg, text=str(seconds), style="Accent.TLabel",
                        font=("TkDefaultFont", 16, "bold"))
        lbl.pack(pady=(0, 8))

        def fire_now():
            self._pending_countdown = None
            dlg.destroy()
            ok, msg = fire()
            self._log("自动%s：%s" % (action_name, msg))

        def cancel(_e=None):
            self._pending_countdown = None
            dlg.destroy()
            self._log("已取消自动%s" % action_name)

        btns = ttk.Frame(dlg)
        btns.pack(pady=(0, 14))
        ttk.Button(btns, text="取消%s" % action_name, command=cancel).pack(
            side="left", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        self._pending_countdown = (dlg, cancel)
        self._log("任务完成：%d 秒后自动%s（点「取消%s」可撤）"
                  % (seconds, action_name, action_name))

        def tick():
            if not dlg.winfo_exists():
                return
            left["n"] -= 1
            if left["n"] <= 0:
                fire_now()
                return
            lbl.configure(text=str(left["n"]))
            dlg.after(1000, tick)

        dlg.after(1000, tick)

    def _cancel_pending_countdown(self):
        """新渲染开始前撤掉挂起的关机/睡眠倒计时。

        不撤的话："渲完弹 60 秒倒计时 → 用户这期间配好新任务点开始 → 0 秒一到
        机器把新渲染关掉"。这是本功能最拧巴的隐藏路径（2026-10-05 复查发现）。
        """
        pending = self._pending_countdown
        if pending is None:
            return
        dlg, _cancel = pending
        self._pending_countdown = None
        if dlg.winfo_exists():
            dlg.destroy()
        self._log("已撤销上一次的自动关机/睡眠（开始了新任务）")
