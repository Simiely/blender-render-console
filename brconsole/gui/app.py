# -*- coding: utf-8 -*-
"""主窗口薄壳：装配状态、视图与控制器 mixin，保留事件循环入口。
"""

from .. import gui_view
from ..gui_view_new import TkView
from .common import *  # noqa: F401,F403
from .common import _apply_icon, _stamp  # noqa: F401
from .app_state import AppState
from .form_controller import FormControllerMixin
from .inspect_controller import InspectControllerMixin
from .queue_controller import QueueControllerMixin
from .render_controller import RenderControllerMixin


class App(FormControllerMixin, InspectControllerMixin,
          QueueControllerMixin, RenderControllerMixin):
    def __init__(self, root, demo=False, cmd_factory=None, preset_blend=None,
                 autostart_mode=False):
        self.root = root
        # ⚠️ 形参叫 autostart_mode 而不是 autostart：common 里 import 了同名模块，
        #    叫 autostart 会在 __init__ 里把模块名遮住
        self.state = AppState(demo=demo, preset_blend=preset_blend,
                              autostart_mode=autostart_mode,
                              cmd_factory=cmd_factory)
        self.state.apply_to(self)
        self.view = None                  # _build() 里填入 TkView（旧视图为 None）

        root.title(WINDOW_TITLE)   # 与 applock.focus_window 按标题找窗口共用同一句
        _apply_icon(root)
        root.geometry("1000x840")
        root.minsize(900, 700)
        self._build(root)
        self._autofill_blender()
        self._sync_autostart_ui()
        self._sync_resume_ui()
        self._refresh_queue()             # 启动时显示磁盘上已有的队列
        if demo:
            self._setup_demo()
        elif autostart_mode:
            self.root.after(200, self.try_autostart_resume)
        elif preset_blend:
            self.v_blend.set(preset_blend)
            self.v_output.set(FormModel.complete_output("", preset_blend))
            self.root.after(200, self.on_inspect)
        # 手填/粘贴 Blender 路径时清掉版本下拉的旧选择 —— 界面不能说谎
        self.v_blender.trace_add("write", self._on_blender_var_changed)
        self.root.after(POLL_MS, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build(self, root):
        if os.environ.get("BRC_OLD_UI") == "1":
            gui_view.build(self, root)
            self.view = None          # 旧视图没有实现新契约，保留兼容分支
            return
        from ..gui_view_new import TkView
        self.view = TkView(self, root)

    def _on_mousewheel(self, event):
        w = self.root.winfo_containing(event.x_root, event.y_root)
        path = str(w) if w else ""
        for canvas in (self.prog_canvas, self.right_canvas):
            if canvas is not None and path.startswith(str(canvas)):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return

    def _toggle_module(self, key):
        m = self._modules.get(key)
        if m and m.get("card"):
            m["card"].toggle_card()
            m["expanded"] = m["card"].expanded

    def clear_log(self):
        self.log.clear()
        self._dropped_shown = 0          # 清空后重新记账（log.clear 已把 dropped 归零）
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _on_log_scroll(self, first, last):
        """日志滚动条的"看得见/看不见"切换（Text 的 `yscrollcommand` 回调）。

        内容装得下时 ttk 会把滑块拉满整条槽 —— 一条 10px 的亮灰长条就会常驻在日志右边，
        跟正文抢注意力。这里按可见范围在两套**杆宽一致**的样式之间切：
        装得下 → `Flat.`（滑块与槽同色，等于看不见）；溢出了 → 正常样式（滑块回来）。

        ⚠️ 不能改成"把滚动条控件藏起来"：一藏日志就宽出 10px，`wrap="word"` 立刻重新
        折行，行数一变又会反过来改变"要不要藏"的判定 —— 会来回闪。换样式不动布局。
        """
        self.vs.set(first, last)
        idle = float(first) <= 0.0 and float(last) >= 1.0
        want = theme.SCROLL_FLAT_STYLE if idle else theme.SCROLL_STYLE
        if want != self._sb_style:
            self._sb_style = want
            self.vs.configure(style=want)

    def _poll(self):
        try:
            while True:
                kind, ev = self.q.get_nowait()
                self._handle(kind, ev)
        except queue.Empty:
            pass
        self._flush_log()
        self._refresh()
        self.root.after(POLL_MS, self._poll)

    def _handle(self, kind, ev):
        if kind == "__inspect__":        # 后台读工程配置的结果，不进 ProgressModel
            self._apply_inspect(ev)
            return
        if kind == "job_start":
            self.native_filter.reset()   # 新任务重新记账，别把上一轮的折叠数带过来
        self.prog.on_event(kind, ev)
        if kind == "native":
            if self.v_native.get() and ev.get("line"):
                # 默认开启 + 折叠：采样进度一秒最多留一条，被折掉的数量会附在下一条前面
                for text in self.native_filter.feed(ev["line"]):
                    self.log.add("  | %s" % text)
            elif ev.get("phase"):
                self.log.add("  · %s" % ev["phase"])
            return
        line = event_line(kind, ev)
        if line:
            self.log.add(line)
        if kind in ("job_done", "job_error"):
            # 任务收尾：把最后一段的折叠数交代清楚，否则尾部那截等于被静默吞掉
            for text in self.native_filter.flush():
                self.log.add("  | %s" % text)
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.btn_clear_state.configure(state="normal")
            self.worker = None
            self._refresh_state_label()
            self._settle_task(kind, ev)
            self._finish_notify(kind, ev)
            # 「开始渲染」永远是队列模式：标记当前项后自动进下一项；
            # 关机/睡眠只在**整个队列**结束时判，不在中间项触发。
            # 例外：用户主动停止 → 停整个队列，当前项保留断点，点开始可续跑。
            item = getattr(self, "_current_queue_item", None)
            cancelled = bool(ev.get("cancelled"))
            if item:
                status = (queue_store.STATUS_DONE if kind == "job_done" and ev.get("ok")
                          else queue_store.STATUS_PENDING if cancelled
                          else queue_store.STATUS_FAILED)
                queue_store.set_status(item["id"], status)
                self._log("队列项已标记：%s（%s）" % (
                    status, os.path.basename(item["config"]["blend"])))
            self._last_queue_event = (kind, ev)
            self._refresh_queue()
            if cancelled:
                self._queue_mode = False
                self._log("队列已暂停（当前项保留断点；点「开始渲染」可接着跑）")
            elif queue_store.next_pending() is None:
                self._finish_queue()
            else:
                self.root.after(300, self._run_next_queue_item)

    def _settle_task(self, kind, ev):
        """一轮跑完后处置待办存档。判据在 `guimodel.settle_task_action`（可单测）。"""
        action, reason = settle_task_action(kind, ev)
        if action == "clear":
            if taskstore.clear():
                self._log("全部帧渲完，已清理待办存档（下次开机不会再自动跑）")
        elif taskstore.set_autoresume(False, reason):
            self._log("已撤掉开机自动续跑（原因：%s；断点保留，修好后点「开始渲染」接着跑）"
                      % reason)
        self._sync_resume_ui()

    def _finish_notify(self, kind, ev):
        """渲完/出错时发通知（提示音 + 任务栏闪烁 + toast 尽力而为）。

        过夜渲染人不在屏幕前，结果必须能穿过注意力盲区 —— 竞品 BRQ 的卖点
        "排队→挂机→走人→收通知"。失败也通知：早上应该知道"没渲完"而不是白等。
        demo 自检旁路：假任务不值得打扰（与 aftermath 的旁路对称）。
        """
        if self.demo:
            return
        if kind == "job_done":
            # `ok` 优先：「最后一帧渲完的同一瞬间点了停止」时 ok 和 cancelled 同时为真，
            # 这时说「渲染已取消」与「存档已按完成删掉」互相打脸（见 P-W3）
            if ev.get("ok"):
                title = "渲染完成"
            else:
                title = "渲染已取消" if ev.get("cancelled") else "渲染未完成"
        else:
            title = "渲染出错"
        # 结果文案直接复用进度模型的口径，别再拼一份
        msg = self.prog.status_text()
        r = notify.announce(self.root.winfo_id(), title, msg)
        self._log("%s（通知：提示音 %s · 任务栏闪烁 %s · toast %s）"
                  % (title, "✓" if r.get("beep") else "×",
                     "✓" if r.get("flash") else "×",
                     "✓" if r.get("toast") else "×"))

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

    def _flush_log(self):
        lines = self.log.drain()
        dropped = self.log.dropped
        if not lines and dropped == self._dropped_shown:
            return
        self.txt.configure(state="normal")
        # 丢过的日志必须**说出来**：静默丢日志等于静默丢证据（原生输出开满时会丢）
        if dropped > self._dropped_shown:
            self.txt.insert("end", "[%s] （日志过快，已丢弃最早的 %d 行）\n"
                            % (_stamp(), dropped - self._dropped_shown))
            self._dropped_shown = dropped
        for line in lines:
            self.txt.insert("end", "[%s] %s\n" % (_stamp(), line))
        # 别让日志无限长：超出上限就砍掉前面一半
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > LOG_MAX_LINES:
            self.txt.delete("1.0", "%d.0" % (n - LOG_MAX_LINES // 2))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _refresh(self):
        p = self.prog
        self.pb["value"] = p.fraction * 100
        # 状态 · ETA · 单帧 全在一行里 —— 不再另贴一个右对齐 Label
        tail = " · ".join(x for x in (p.eta_text(), p.per_frame_text()) if x)
        text = p.status_text()
        if self._queue_mode and self._queue_total:
            text = "队列 %d/%d · %s" % (self._queue_index, self._queue_total, text)
        self.lbl_status.configure(text=("%s　|　%s" % (text, tail)) if tail else text)
        self._refresh_metrics(p)

    def _refresh_metrics(self, p):
        """把用时 / 单帧 / 速度 / ETA / 预计完成换算成给人看的一行。"""
        panel = getattr(self, "log_panel", None)
        if panel is None:
            return
        if p.started_at is None:
            elapsed = "—"
        elif p.running:
            elapsed = fmt_duration(time.time() - p.started_at)
        else:
            elapsed = fmt_duration(p.elapsed or 0.0)
        per_frame = fmt_duration(p.per_frame) if p.per_frame else "—"
        speed = ("%.1f 帧/分" % (60.0 / p.per_frame)
                 if p.per_frame and p.per_frame > 0 else "—")
        eta = fmt_duration(p.eta_sec) if p.eta_sec else "—"
        finish = (fmt_clock(time.time() + p.eta_sec)
                  if (p.eta_sec and p.running) else "—")
        panel.set_metrics(elapsed, per_frame, speed, eta, finish)

    def _refresh_state_label(self):
        info = state_summary(guess_state_path(self.v_output.get()))
        if info:
            self.lbl_state.configure(text="断点：已完成 %d/%d 帧"
                                          % (info["done"], info["total"]))
        else:
            self.lbl_state.configure(text="断点：无")

    def _log(self, text):
        self.log.add(text)

    def _setup_demo(self):
        """自检：用假 Blender 顺序跑两个示例队列项（含多段不连续帧）。

        demo 写自己的临时目录，且**先清空队列**，避免把用户真实队列拿来跑。
        """
        import tempfile
        d = tempfile.mkdtemp(prefix="brc-demo-")
        # 存档整体重定向进 demo 临时目录：demo 绝不能把示例项（和 python.exe 假
        # Blender 路径）写进用户真实 queue.json —— 那会污染后续所有读取与渲染
        os.environ["BRC_STORE_DIR"] = os.path.join(d, "store")
        queue_store.save([])
        # demo 走假 Blender，这里只需一个"存在"的可执行文件满足 core 的校验
        exe = sys.executable
        if not self.v_blender.get():
            self.v_blender.set(exe)
        specs = (("demo_a", [new_segment("1", "4"), new_segment("9", "10")]),
                 ("demo_b", [new_segment("2", "3")]))
        first_blend = ""
        for name, segs in specs:
            blend = os.path.join(d, name + ".blend")
            with open(blend, "wb") as f:
                f.write(b"BLENDER-v5")
            fm = FormModel(blend=blend, blender=exe,
                           output=os.path.join(d, "out", name + "_####"),
                           segments=segs)
            cfg, errors, _warns = fm.to_config()
            if errors:
                self.log.add("! demo 配置无效：%s" % "；".join(errors))
                continue
            queue_store.add(cfg, exe)
            if not first_blend:
                first_blend = blend
        q = queue_store.load()
        self._selected_queue_id = q["items"][0]["id"]
        self._apply_queue_item(q["items"][0])
        self._inspected_path = first_blend   # 假工程，别真去调 Blender 读配置
        self.lbl_state.configure(
            text="demo 模式：假 Blender 顺序跑 %d 项（第一项分 1-4、9-10 两段）"
                 % len(q["items"]))
        self.root.after(300, self.on_start)


def run_gui(demo=False, cmd_factory=None, preset_blend=None, autostart_mode=False):
    """构造并启动界面（阻塞直到窗口关闭）。"""
    # ---- 单实例 ----
    # 渲染独占 GPU，且两个实例会互踩断点文件与输出图（Blender 官方 #37974 同款问题），
    # 所以界面只允许一个。方案用命名互斥量（微软推荐：崩溃自动回收、原子、无 PID 复用坑），
    # 不用锁文件 —— 残锁会把用户挡在门外。
    # `--demo`（自检）旁路：它写自己的临时目录，要能与真实例并存。
    lock = None
    root = tk.Tk()
    if demo:
        # 进 App 前就重定向存档：App 初始化早期（续跑认领等）就会读 pending
        import tempfile as _tf
        os.environ["BRC_STORE_DIR"] = os.path.join(
            _tf.mkdtemp(prefix="brc-demo-store-"), "store")
    try:
        # DPI 校正要在套主题**之前**：主题里的复选框指示器是图片元素，
        # 图片不会跟着缩放自动变大 —— 必须在建它的时候就知道当前 DPI。
        root.tk.call("tk", "scaling", 1.3)      # 高 DPI 下别糊
    except Exception:
        pass
    theme.apply(root)                           # 深色主题要在建控件之前套上
    if not demo:
        ok, lock = applock.acquire()
        if not ok:
            # 已有实例在跑：把那个窗口调到前面，让用户明白"为什么没开出新窗口"。
            # 调不到（互斥量在、窗口不在的罕见情形）才弹框说明。
            if not applock.focus_window(WINDOW_TITLE):
                messagebox.showwarning(
                    "程序已经在运行",
                    "blender-render-console 已经有一个窗口在运行了（可能是开机自启拉起的"
                    "那个，正在续跑任务）。\n\n请到那个窗口里操作；本窗口即将关闭。",
                    parent=root)
            root.destroy()
            return 0
    try:
        App(root, demo=demo, cmd_factory=cmd_factory, preset_blend=preset_blend,
            autostart_mode=autostart_mode)
        root.mainloop()
    finally:
        applock.release(lock)
    return 0
