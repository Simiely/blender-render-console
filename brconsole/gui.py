# -*- coding: utf-8 -*-
"""tkinter 界面层。

规则（改这个文件前先看 `guimodel.py`）：
- **业务逻辑一律不在界面里**：表单→配置、事件→状态字段、日志缓冲都在 `guimodel`
- **线程模型**：`RenderJob.run()` 跑在后台线程，它的 `on_event` 只往 `queue.Queue` 里塞；
  主线程用 `root.after` 定时取队列并刷控件。tkinter 不是线程安全的，
  绝不能从工作线程直接改控件。
- **停止**：`job.cancel()`（杀进程树 + 置取消标志），进度已按帧落盘，下次能续跑。

启动：
    python main.py                 # 无位置参数 → 图形界面
    python main.py --demo          # 图形界面 + 自动跑一轮模拟任务（自检/截图用）
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import locate, theme
from .cli import parse_frames
from .core import RenderJob
from .guimodel import (DEVICES, ENGINES, FORMATS, RESTART_VALUES, FormModel,
                       LogModel, ProgressModel, event_line, guess_state_path,
                       state_summary)
from .inspect import output_template_from, read_blend_info, summarize as summarize_blend

POLL_MS = 80
LOG_MAX_LINES = 4000
TITLE = "blender-render-console"


def _stamp():
    return time.strftime("%H:%M:%S")


class App(object):
    """主窗口。"""

    def __init__(self, root, demo=False, cmd_factory=None, preset_blend=None):
        self.root = root
        self.demo = demo
        self.preset_blend = preset_blend
        self.cmd_factory = cmd_factory          # 仅 demo/自检时注入假 Blender
        self.form = FormModel()
        self.prog = ProgressModel()
        self.log = LogModel()
        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.stopping = False
        self._inspected_path = None      # 已经读过配置的工程，避免重复读
        self._inspecting = False

        root.title("%s · 无头渲染控制台" % TITLE)
        root.geometry("1000x840")
        root.minsize(900, 700)
        self._build(root)
        self._autofill_blender()
        if demo:
            self._setup_demo()
        elif preset_blend:
            # 命令行直接把工程带进来：填好并自动读一次配置
            self.v_blend.set(preset_blend)
            self.v_output.set(FormModel.complete_output("", preset_blend))
            self.root.after(200, self.on_inspect)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._poll)

    # ---------------- 界面搭建 ----------------
    def _build(self, root):
        pad = {"padx": 6, "pady": 4}
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # ---- 任务配置 ----
        box = ttk.LabelFrame(root, text="任务")
        box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        box.columnconfigure(1, weight=1)          # 只有主输入列吃掉多余宽度

        self.v_blend = tk.StringVar()
        self.v_blender = tk.StringVar()
        self.v_output = tk.StringVar()
        self.v_frames = tk.StringVar()
        self.v_start = tk.StringVar(value="1")
        self.v_end = tk.StringVar(value="250")
        self.v_step = tk.StringVar(value="1")
        self.v_engine = tk.StringVar(value=ENGINES[0][1])
        self.v_samples = tk.StringVar()
        self.v_device = tk.StringVar(value=DEVICES[0][1])
        self.v_w = tk.StringVar()
        self.v_h = tk.StringVar()
        self.v_pct = tk.StringVar()
        self.v_format = tk.StringVar(value=FORMATS[0][1])
        self.v_resume = tk.BooleanVar(value=True)
        self.v_native = tk.BooleanVar(value=False)
        self.v_restarts = tk.StringVar(value="5")
        self.v_no_progress = tk.StringVar(value="3")
        self.v_attempts = tk.StringVar(value="3")

        r = 0
        ttk.Label(box, text="工程 .blend").grid(row=r, column=0, sticky="w", **pad)
        self.ent_blend = ttk.Entry(box, textvariable=self.v_blend)
        self.ent_blend.grid(row=r, column=1, columnspan=3, sticky="ew", **pad)
        self.ent_blend.bind("<FocusOut>", lambda _e: self.maybe_inspect())
        ttk.Button(box, text="浏览…", width=9, command=self.pick_blend).grid(
            row=r, column=4, **pad)

        r += 1
        ttk.Label(box, text="Blender").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.v_blender).grid(row=r, column=1, columnspan=3,
                                                         sticky="ew", **pad)
        ttk.Button(box, text="自动探测", width=9, command=self.autofill_blender).grid(
            row=r, column=4, **pad)

        r += 1
        ttk.Label(box, text="帧范围").grid(row=r, column=0, sticky="w", **pad)
        f = ttk.Frame(box)
        f.grid(row=r, column=1, columnspan=3, sticky="w")
        ttk.Label(f, text="起").pack(side="left")
        ttk.Entry(f, textvariable=self.v_start, width=7).pack(side="left", padx=2)
        ttk.Label(f, text="止").pack(side="left", padx=(8, 0))
        ttk.Entry(f, textvariable=self.v_end, width=7).pack(side="left", padx=2)
        ttk.Label(f, text="步长").pack(side="left", padx=(8, 0))
        ttk.Entry(f, textvariable=self.v_step, width=4).pack(side="left", padx=2)
        ttk.Label(f, text="帧列表（填了就只用它）").pack(side="left", padx=(14, 0))
        ttk.Entry(f, textvariable=self.v_frames, width=20).pack(side="left", padx=2)

        r += 1
        ttk.Label(box, text="引擎 / 采样").grid(row=r, column=0, sticky="w", **pad)
        g = ttk.Frame(box)
        g.grid(row=r, column=1, columnspan=3, sticky="w")
        ttk.Combobox(g, textvariable=self.v_engine, width=20, state="readonly",
                     values=[v for _, v in ENGINES]).pack(side="left")
        ttk.Label(g, text="采样").pack(side="left", padx=(12, 2))
        ttk.Entry(g, textvariable=self.v_samples, width=8).pack(side="left")
        ttk.Label(g, text="（留空＝保持工程设置）", foreground="#9aa0a6").pack(
            side="left", padx=(6, 0))

        r += 1
        ttk.Label(box, text="设备 / 分辨率").grid(row=r, column=0, sticky="w", **pad)
        h = ttk.Frame(box)
        h.grid(row=r, column=1, columnspan=3, sticky="w")
        ttk.Combobox(h, textvariable=self.v_device, width=10, state="readonly",
                     values=[v for _, v in DEVICES]).pack(side="left")
        ttk.Label(h, text="宽").pack(side="left", padx=(12, 2))
        ttk.Entry(h, textvariable=self.v_w, width=7).pack(side="left")
        ttk.Label(h, text="高").pack(side="left", padx=(8, 2))
        ttk.Entry(h, textvariable=self.v_h, width=7).pack(side="left")
        ttk.Label(h, text="百分比%").pack(side="left", padx=(12, 2))
        ttk.Entry(h, textvariable=self.v_pct, width=5).pack(side="left")
        ttk.Label(h, text="格式").pack(side="left", padx=(12, 2))
        ttk.Combobox(h, textvariable=self.v_format, width=12, state="readonly",
                     values=[v for _, v in FORMATS]).pack(side="left")

        r += 1
        ttk.Label(box, text="输出模板").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.v_output).grid(row=r, column=1, columnspan=3,
                                                        sticky="ew", **pad)
        ttk.Button(box, text="选择目录", width=9, command=self.pick_output).grid(
            row=r, column=4, **pad)

        r += 1
        opts = ttk.Frame(box)
        opts.grid(row=r, column=1, columnspan=3, sticky="w")
        ttk.Checkbutton(opts, text="从断点续跑（未完成的帧接着渲染）",
                        variable=self.v_resume).pack(side="left")
        ttk.Label(opts, text="崩溃后重启").pack(side="left", padx=(12, 2))
        ttk.Combobox(opts, textvariable=self.v_restarts, width=18, state="readonly",
                     values=RESTART_VALUES).pack(side="left")
        ttk.Label(opts, text="单帧最多尝试").pack(side="left", padx=(12, 2))
        ttk.Entry(opts, textvariable=self.v_attempts, width=4).pack(side="left")

        r += 1
        opts_b = ttk.Frame(box)
        opts_b.grid(row=r, column=1, columnspan=3, sticky="w")
        ttk.Label(opts_b, text="连续这么多轮一帧都没推进就停").pack(side="left")
        ttk.Entry(opts_b, textvariable=self.v_no_progress, width=4).pack(side="left", padx=3)
        ttk.Label(opts_b, text="轮（“一直重启”的兜底，0=不限制）",
                  style="Muted.TLabel").pack(side="left", padx=(2, 0))

        r += 1
        opts2 = ttk.Frame(box)
        opts2.grid(row=r, column=1, columnspan=3, sticky="w")
        ttk.Checkbutton(opts2, text="显示 Blender 原生输出（排错用，日志会长很多）",
                        variable=self.v_native).pack(side="left")
        # 说明：下拉的取值 "unlimited" 是给 parse_restart_limit 读的，界面上显示的是
        # 「一直重启，直到全部渲完」，所以旁边补一句人话解释
        self.lbl_restart_note = ttk.Label(
            opts2, text="选「一直重启，直到全部渲完」= 次数不限（仍受上面无进展轮数兜底）",
            style="Muted.TLabel")
        self.lbl_restart_note.pack(side="left", padx=(16, 0))

        r += 1
        info = ttk.Frame(box)
        info.grid(row=r, column=1, columnspan=3, sticky="ew")
        self.btn_inspect = ttk.Button(info, text="读取工程配置", width=14,
                                      command=self.on_inspect)
        self.btn_inspect.pack(side="left")
        self.lbl_inspect = ttk.Label(info, text="选好工程后会自动读一次（引擎/采样/"
                                                "分辨率/帧范围/输出路径）",
                                     style="Muted.TLabel")
        self.lbl_inspect.pack(side="left", padx=10)

        # ---- 控制条 ----
        bar = ttk.Frame(root)
        bar.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        self.btn_start = ttk.Button(bar, text="开始渲染", style="Accent.TButton",
                                    command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self.on_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(bar, text="打开输出目录", command=self.open_output_dir).pack(side="left")
        ttk.Button(bar, text="清空断点", command=self.clear_state).pack(side="left", padx=6)
        ttk.Button(bar, text="清空日志", command=self.clear_log).pack(side="left")
        self.lbl_state = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_state.pack(side="right")

        # ---- 进度 ----
        prog = ttk.LabelFrame(root, text="进度")
        prog.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        prog.columnconfigure(0, weight=1)
        prog.rowconfigure(2, weight=1)

        self.pb = ttk.Progressbar(prog, orient="horizontal", mode="determinate",
                                  maximum=100)
        self.pb.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        self.lbl_status = ttk.Label(prog, text="就绪")
        self.lbl_status.grid(row=1, column=0, sticky="w", padx=6)
        self.lbl_eta = ttk.Label(prog, text="", style="Accent.TLabel")
        self.lbl_eta.grid(row=1, column=1, sticky="e", padx=6)

        self.txt = tk.Text(prog, height=12, wrap="none", state="disabled",
                           font=("Consolas", 9), relief="flat",
                           **theme.log_widget_colors())
        vs = ttk.Scrollbar(prog, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=vs.set)
        self.txt.grid(row=2, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vs.grid(row=2, column=1, sticky="ns", pady=6)

        # ---- 底部说明 ----
        ttk.Label(root, text="渲染走 `blender -b` 无头模式，不改动你的 .blend；"
                             "崩溃或停止后再次开始即可从断点继续。",
                  style="Muted.TLabel").grid(row=3, column=0, sticky="w",
                                             padx=10, pady=(0, 8))

    # ---------------- 表单动作 ----------------
    def _autofill_blender(self):
        try:
            cands = locate.find_blender()
        except Exception:
            cands = []
        if cands:
            self.v_blender.set(cands[0])
            if len(cands) > 1:
                self._log("检测到多个 Blender，使用 %s（其余：%s）"
                          % (cands[0], ", ".join(cands[1:3])))

    def autofill_blender(self):
        old = self.v_blender.get()
        self._autofill_blender()
        if not self.v_blender.get():
            messagebox.showwarning("没找到 Blender",
                                   "自动扫描没找到 blender.exe，请手动指定路径。")
        elif old and old != self.v_blender.get():
            self._log("Blender 改为：%s" % self.v_blender.get())

    def pick_blend(self):
        path = filedialog.askopenfilename(
            title="选择 .blend 工程",
            filetypes=[("Blender 工程", "*.blend"), ("所有文件", "*.*")])
        if path:
            self.v_blend.set(path)
            if not self.v_output.get():
                self.v_output.set(FormModel.complete_output("", path))
            self.on_inspect()             # 选完立刻把工程里的渲染配置读出来

    def maybe_inspect(self):
        """手填/粘贴路径后失焦时也试着读一次（同一个工程不重复读）。"""
        p = self.v_blend.get().strip()
        if p and os.path.exists(p) and p != self._inspected_path:
            self.on_inspect()

    # ---------------- 读取工程里的渲染配置 ----------------
    def on_inspect(self):
        if self._inspecting or self.prog.running:
            return                        # 渲染中别去抢 Blender
        blend = self.v_blend.get().strip()
        if not blend or not os.path.exists(blend):
            messagebox.showwarning("还没选工程", "先选一个 .blend 工程文件。")
            return
        blender = self.v_blender.get().strip()
        if not blender or not os.path.exists(blender):
            self._autofill_blender()
            blender = self.v_blender.get().strip()
        if not blender or not os.path.exists(blender):
            messagebox.showwarning("缺少 Blender 路径",
                                   "读取工程配置需要先指定 blender.exe。")
            return

        self._inspecting = True
        self._inspected_path = blend
        self.btn_inspect.configure(state="disabled")
        self.lbl_inspect.configure(text="正在读取工程配置…", style="Muted.TLabel")
        self.log.add("读取工程配置：%s" % os.path.basename(blend))
        threading.Thread(target=self._inspect_worker, args=(blender, blend),
                         daemon=True).start()

    def _inspect_worker(self, blender, blend):
        try:
            info = read_blend_info(blender, blend, timeout=300)
        except Exception as e:
            info = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.q.put(("__inspect__", info))

    def _apply_inspect(self, info):
        self._inspecting = False
        self.btn_inspect.configure(state="normal")
        if not info or not info.get("ok"):
            msg = (info or {}).get("error", "未知错误")
            self.lbl_inspect.configure(text="读取失败（可手填下面的参数）",
                                       style="Muted.TLabel")
            self.log.add("! 读取工程配置失败：%s" % msg.replace("\n", " "))
            return

        self.v_start.set(str(info.get("frame_start", self.v_start.get())))
        self.v_end.set(str(info.get("frame_end", self.v_end.get())))
        self.v_step.set(str(info.get("frame_step", 1)))
        engine = (info.get("engine") or "").upper()
        if engine in [v for _, v in ENGINES]:
            self.v_engine.set(engine)
        if info.get("samples"):
            self.v_samples.set(str(info["samples"]))
        # 设备：只有能确定才改（Cycles 的 GPU 后端名来自偏好设置）
        if (info.get("cycles_device") or "").upper() == "CPU":
            self.v_device.set("CPU")
        else:
            cdt = (info.get("compute_device_type") or "").upper()
            if cdt in [v for _, v in DEVICES]:
                self.v_device.set(cdt)
        res = info.get("resolution") or []
        if len(res) == 2:
            self.v_w.set(str(res[0]))
            self.v_h.set(str(res[1]))
        if info.get("resolution_percentage"):
            self.v_pct.set(str(info["resolution_percentage"]))
        fmt = (info.get("file_format") or "").upper()
        if fmt in [v for _, v in FORMATS]:
            self.v_format.set(fmt)
        tpl = output_template_from(info.get("output_path"), self.v_blend.get().strip())
        if tpl:
            self.v_output.set(tpl)

        short = "%s · %s · %sx%s · 采样 %s · 帧 %s-%s" % (
            info.get("engine"), info.get("scene"),
            (res or [0, 0])[0], (res or [0, 0])[1], info.get("samples"),
            info.get("frame_start"), info.get("frame_end"))
        self.lbl_inspect.configure(text="已读取：" + short, style="OK.TLabel")
        self.log.add("已读取工程配置：" + summarize_blend(info))
        scenes = info.get("scenes") or []
        if len(scenes) > 1:
            self.log.add("注意：工程里有多个场景 %s，当前只读第一条（%s）"
                         % (scenes, info.get("scene")))

    def pick_output(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.v_output.set(os.path.join(d, "frame_####"))

    def open_output_dir(self):
        d = os.path.dirname(os.path.abspath(self.v_output.get() or "."))
        if os.path.isdir(d):
            try:
                os.startfile(d)                 # Windows
            except Exception as e:
                self._log("打不开目录：%r" % (e,))
        else:
            messagebox.showinfo("目录还不存在", "输出目录还没创建：%s" % d)

    def clear_state(self):
        p = guess_state_path(self.v_output.get())
        if os.path.exists(p):
            try:
                os.remove(p)
                self._log("已清空断点：%s" % p)
            except OSError as e:
                messagebox.showerror("删除失败", str(e))
        else:
            self._log("没有断点文件（%s）" % p)
        self._refresh_state_label()

    def clear_log(self):
        self.log.clear()
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    # ---------------- 启动 / 停止 ----------------
    def _collect_form(self):
        self.form.blend = self.v_blend.get()
        self.form.blender = self.v_blender.get()
        self.form.output = self.v_output.get()
        self.form.frames = self.v_frames.get()
        self.form.start = self.v_start.get()
        self.form.end = self.v_end.get()
        self.form.step = self.v_step.get()
        self.form.engine = self.v_engine.get()
        self.form.samples = self.v_samples.get()
        self.form.device = self.v_device.get()
        self.form.width = self.v_w.get()
        self.form.height = self.v_h.get()
        self.form.pct = self.v_pct.get()
        self.form.file_format = self.v_format.get()
        self.form.resume = self.v_resume.get()
        self.form.max_restarts = self.v_restarts.get()
        self.form.max_no_progress = self.v_no_progress.get()
        self.form.max_frame_attempts = self.v_attempts.get()
        self.form.show_native = self.v_native.get()

    def on_start(self):
        self._collect_form()
        cfg, errors, warns = self.form.to_config(parse_frames)
        for w in warns:
            self._log("! %s" % w)
        if errors:
            messagebox.showerror("配置有问题", "\n".join("· " + e for e in errors))
            return
        blender = (self.form.blender or "").strip()
        if not blender:
            messagebox.showerror("配置有问题", "还没指定 blender.exe（点「自动探测」或手填）")
            return
        if not os.path.exists(blender):
            messagebox.showerror("配置有问题", "blender.exe 不存在：%s" % blender)
            return

        self.job = RenderJob(cfg, blender, cmd_factory=self.cmd_factory)
        self.stopping = False
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.pb["value"] = 0
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self._log("开始渲染：%s（%d 帧）" % (cfg.blend, len(cfg.frames)))

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
        self.btn_stop.configure(state="disabled")
        self._log("正在停止…（已完成的部分已保存，下次可从断点继续）")

    def on_close(self):
        if self.job is not None and self.prog.running:
            if not messagebox.askokcancel("任务还在跑", "停止并退出？进度已保存。",
                                          parent=self.root):
                return
            self.job.cancel()
            if self.worker and self.worker.is_alive():
                self.worker.join(timeout=5)
        self.root.destroy()

    # ---------------- 事件循环 ----------------
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
        self.prog.on_event(kind, ev)
        if kind == "native":
            if self.v_native.get() and ev.get("line"):
                self.log.add("  | %s" % ev["line"])
            elif ev.get("phase"):
                self.log.add("  · %s" % ev["phase"])
            return
        line = event_line(kind, ev)
        if line:
            self.log.add(line)
        if kind in ("job_done", "job_error"):
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.worker = None
            self._refresh_state_label()

    def _flush_log(self):
        lines = self.log.drain()
        if not lines:
            return
        self.txt.configure(state="normal")
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
        self.lbl_status.configure(text=p.status_text())
        tail = " · ".join(x for x in (p.eta_text(), p.per_frame_text()) if x)
        self.lbl_eta.configure(text=tail)

    def _refresh_state_label(self):
        info = state_summary(guess_state_path(self.v_output.get()))
        if info:
            self.lbl_state.configure(text="断点：已完成 %d/%d 帧"
                                          % (info["done"], info["total"]))
        else:
            self.lbl_state.configure(text="断点：无")

    def _log(self, text):
        self.log.add(text)

    # ---------------- demo（自检/截图用）----------------
    def _setup_demo(self):
        """填一套可直接跑的示例配置，并用假 Blender 跑一轮（不走真 Blender）。"""
        import tempfile
        d = tempfile.mkdtemp(prefix="brc-demo-")
        blend = os.path.join(d, "demo.blend")
        with open(blend, "wb") as f:
            f.write(b"BLENDER-v5")
        self.v_blend.set(blend)
        # demo 走假 Blender，这里只需一个"存在"的可执行文件满足 core 的校验
        if not self.v_blender.get():
            self.v_blender.set(sys.executable)
        self.v_start.set("1")
        self.v_end.set("6")
        self.v_output.set(os.path.join(d, "out", "frame_####"))
        self._inspected_path = blend      # 假工程，别真去调 Blender 读配置
        self.lbl_state.configure(text="demo 模式：用假 Blender 模拟 6 帧")
        self.root.after(300, self.on_start)


def run_gui(demo=False, cmd_factory=None, preset_blend=None):
    """构造并启动界面（阻塞直到窗口关闭）。"""
    root = tk.Tk()
    theme.apply(root)                           # 深色主题要在建控件之前套上
    try:
        root.tk.call("tk", "scaling", 1.3)      # 高 DPI 下别糊
    except Exception:
        pass
    App(root, demo=demo, cmd_factory=cmd_factory, preset_blend=preset_blend)
    root.mainloop()
    return 0
