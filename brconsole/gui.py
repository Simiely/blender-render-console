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

from . import autostart, locate, taskstore, theme
from .cli import parse_frames
from .core import RenderJob
from .guimodel import (DEFAULT_RESTART_OPTION, DEVICES, ENGINES, FORMATS,
                       RESTART_OPTIONS, SCENE_DEFAULT_LABEL, SCENE_NEED_READ_LABEL,
                       SCENE_PLACEHOLDERS, FormModel, LogModel, ProgressModel,
                       event_line, fields_from_detail, guess_state_path, scene_to_config,
                       state_summary)
from .inspect import (read_blend_info, scene_detail, scene_names,
                      summarize as summarize_blend)

POLL_MS = 80
LOG_MAX_LINES = 4000
TITLE = "blender-render-console"
AUTOSTART_FLAG = autostart.AUTOSTART_FLAG


def _stamp():
    return time.strftime("%H:%M:%S")


def icon_path():
    """app.ico 的位置：源码运行在仓库 `assets/` 下，打包后在 `_brc/assets/` 下。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        return os.path.join(base, "_brc", "assets", "app.ico")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "app.ico")


def _apply_icon(root):
    """设窗口/任务栏图标。

    exe 自己的图标由打包时写进 PE 资源，但 **Tk 窗口不会自动继承** —— 不设的话
    任务栏和标题栏会显示 Tk 自带的羽毛图标。用 `default=` 让后续弹出的子窗口也带上。
    图标缺失或格式不对不该影响功能，所以整体吞异常。
    """
    try:
        p = icon_path()
        if os.path.exists(p):
            root.iconbitmap(default=p)
    except Exception:
        pass


class App(object):
    """主窗口。"""

    def __init__(self, root, demo=False, cmd_factory=None, preset_blend=None,
                 autostart_mode=False):
        self.root = root
        self.demo = demo
        self.preset_blend = preset_blend
        # ⚠️ 形参叫 autostart_mode 而不是 autostart：本模块 import 了同名模块，
        #    叫 autostart 会在 __init__ 里把模块名遮住
        self.autostart_mode = autostart_mode
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
        self._scene_info = None          # 最近一次读到的工程配置（含各场景详情）

        root.title("%s · 无头渲染控制台" % TITLE)
        _apply_icon(root)
        root.geometry("1000x840")
        root.minsize(900, 700)
        self._build(root)
        self._autofill_blender()
        self._sync_autostart_ui()
        if demo:
            self._setup_demo()
        elif autostart_mode:
            # 开机自启拉起：认领未完成任务（下面的 after 要等控件都建好、布局完再跑）
            self.root.after(200, self.try_autostart_resume)
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
        self.v_scene = tk.StringVar(value=SCENE_NEED_READ_LABEL)
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
        self.v_restarts = tk.StringVar(value=DEFAULT_RESTART_OPTION)
        self.v_no_progress = tk.StringVar(value="3")
        self.v_attempts = tk.StringVar(value="3")
        # 开机自启勾选态**不从记忆读**，而是下面按注册表实际登记情况回填 ——
        # 界面必须反映"真的会自启吗"，不能显示一个自己记的、可能已经失效的状态
        self.v_autostart = tk.BooleanVar(value=False)
        self.v_auto_resume = tk.BooleanVar(value=True)

        r = 0
        ttk.Label(box, text="工程 .blend").grid(row=r, column=0, sticky="w", **pad)
        self.ent_blend = ttk.Entry(box, textvariable=self.v_blend)
        self.ent_blend.grid(row=r, column=1, columnspan=3, sticky="ew", **pad)
        self.ent_blend.bind("<FocusOut>", lambda _e: self.maybe_inspect())
        ttk.Button(box, text="浏览…", width=9, command=self.pick_blend).grid(
            row=r, column=4, **pad)

        r += 1
        ttk.Label(box, text="场景").grid(row=r, column=0, sticky="w", **pad)
        self.cmb_scene = ttk.Combobox(box, textvariable=self.v_scene, width=26,
                                      state="disabled", values=[SCENE_NEED_READ_LABEL])
        self.cmb_scene.grid(row=r, column=1, sticky="w", **pad)
        self.cmb_scene.bind("<<ComboboxSelected>>", self.on_scene_change)
        self.lbl_scene = ttk.Label(
            box, text="读取工程配置后可用；工程里只有一个场景时不用管它",
            style="Muted.TLabel")
        self.lbl_scene.grid(row=r, column=2, columnspan=2, sticky="w", **pad)

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
        self.cb_restarts = ttk.Combobox(opts, textvariable=self.v_restarts, width=22,
                                        state="readonly", values=RESTART_OPTIONS)
        self.cb_restarts.pack(side="left")
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
        # 下拉里放的就是中文文案本身（不另设"显示值/实际值"两张表），
        # 由 core.parse_restart_limit 认中文，这里补一句人话解释即可
        self.lbl_restart_note = ttk.Label(
            opts2, text="选「一直重启，直到全部渲完」= 次数不限（仍受上面无进展轮数兜底）",
            style="Muted.TLabel")
        self.lbl_restart_note.pack(side="left", padx=(16, 0))

        # ---- 开机自启 / 自动续跑 ----
        r += 1
        boot = ttk.Frame(box)
        boot.grid(row=r, column=1, columnspan=3, sticky="w")
        self.chk_autostart = ttk.Checkbutton(
            boot, text="开机自动启动", variable=self.v_autostart,
            command=self.on_toggle_autostart)
        self.chk_autostart.pack(side="left")
        ttk.Checkbutton(boot, text="启动时自动续跑未完成任务",
                        variable=self.v_auto_resume).pack(side="left", padx=(12, 0))
        self.lbl_autostart = ttk.Label(boot, text="", style="Muted.TLabel")
        self.lbl_autostart.pack(side="left", padx=10)

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
        # 换工程了：旧工程的场景列表立刻作废，读完再填（否则会拿旧场景名去渲染）
        self._scene_info = None
        self._reset_scene_combo()
        self.btn_inspect.configure(state="disabled")
        self.lbl_inspect.configure(text="正在读取工程配置…", style="Muted.TLabel")
        self.log.add("读取工程配置：%s（一次会把工程里所有场景都读出来）"
                     % os.path.basename(blend))
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
            self._scene_info = None
            self._reset_scene_combo()
            return

        self._scene_info = info
        names = scene_names(info)
        active = info.get("scene") or ""
        # 顶层字段就是「激活场景」那一份，直接拿来填表单
        self._fill_from_detail(info, active)

        res = info.get("resolution") or [0, 0]
        self.lbl_inspect.configure(
            text="已读取：%s · 场景 %s · %sx%s · 采样 %s · 帧 %s-%s" % (
                info.get("engine"), active, res[0], res[1],
                "-" if info.get("samples") is None else info.get("samples"),
                info.get("frame_start"), info.get("frame_end")),
            style="OK.TLabel")
        self.log.add("已读取工程配置：" + summarize_blend(info))
        self._update_scene_combo(names, active)
        self._report_scene_camera(info, active)

    def _fill_from_detail(self, detail, name=""):
        """按一个场景的配置重填表单（引擎/采样/分辨率/帧范围/输出路径都是 per-scene 的）。"""
        fields = fields_from_detail(detail, self.v_blend.get().strip())
        for key, var in (("start", self.v_start), ("end", self.v_end),
                         ("step", self.v_step), ("engine", self.v_engine),
                         ("samples", self.v_samples), ("device", self.v_device),
                         ("width", self.v_w), ("height", self.v_h),
                         ("pct", self.v_pct), ("file_format", self.v_format),
                         ("output", self.v_output)):
            if key in fields:
                var.set(fields[key])
        return fields

    def _update_scene_combo(self, names, active):
        """场景下拉：只有一个场景时只显示名字，多个时可切换。"""
        if not names:
            self._reset_scene_combo()
            return
        self.cmb_scene.configure(values=names, state="readonly")
        self.v_scene.set(active if active in names else names[0])
        if len(names) == 1:
            self.lbl_scene.configure(text="工程里只有这一个场景", style="Muted.TLabel")
        else:
            self.lbl_scene.configure(
                text="工程有 %d 个场景，可切换（切换后参数按该场景重填）" % len(names),
                style="OK.TLabel")
            self.log.add("注意：工程里有 %d 个场景：%s。当前用「%s」（Blender 里激活的那个），"
                         "可在「场景」里切换。" % (len(names), "、".join(names), active))

    def _reset_scene_combo(self):
        self.cmb_scene.configure(values=[SCENE_NEED_READ_LABEL], state="disabled")
        self.v_scene.set(SCENE_NEED_READ_LABEL)
        self.lbl_scene.configure(text="读取工程配置后可用；工程里只有一个场景时不用管它",
                                 style="Muted.TLabel")

    def _report_scene_camera(self, detail, name):
        """没有相机的场景 Blender 会直接拒绝渲染 —— 早点说，别等按下开始才报错。"""
        if detail and not detail.get("camera"):
            self.log.add("! 场景「%s」没有设置相机，渲染这个场景会失败（在 Blender 里给它指定相机）"
                         % name)

    def on_scene_change(self, _event=None):
        """切换场景：按该场景的配置重填表单（不用再起一次 Blender）。"""
        name = self.v_scene.get()
        if name in SCENE_PLACEHOLDERS:
            return
        info = self._scene_info or {}
        if os.path.abspath(info.get("blend") or "") != os.path.abspath(self.v_blend.get().strip()):
            self.log.add("! 换过工程了，请点「读取工程配置」重新读一次再切换场景")
            return
        detail = scene_detail(info, name)
        if detail is None:
            self.log.add("! 读到的配置里没有场景「%s」，请点「读取工程配置」重新读一次" % name)
            return

        self._fill_from_detail(detail, name)
        res = detail.get("resolution") or [0, 0]
        self.lbl_inspect.configure(
            text="场景 %s：%s · %sx%s · 采样 %s · 帧 %s-%s" % (
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
    # ---------------- 开机自启 / 任务存档 ----------------
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

    def _apply_form_model(self, fm):
        """把 `FormModel` 铺到控件上（开机续跑回填用）。"""
        self.form = fm
        self.v_blend.set(fm.blend)
        self.v_blender.set(fm.blender)
        self.v_output.set(fm.output)
        self.v_frames.set(fm.frames)
        self.v_start.set(fm.start)
        self.v_end.set(fm.end)
        self.v_step.set(fm.step)
        self.v_engine.set(fm.engine)
        self.v_samples.set(fm.samples)
        self.v_device.set(fm.device)
        self.v_w.set(fm.width)
        self.v_h.set(fm.height)
        self.v_pct.set(fm.pct)
        self.v_format.set(fm.file_format)
        self.v_scene.set(fm.scene)
        self.v_resume.set(fm.resume)
        # 重启次数下拉是 readonly：候选里没有这个文案的话，set 会被控件**静默忽略**，
        # 界面上留着上一个值 —— 所以先补候选再 set
        values = list(self.cb_restarts["values"])
        if fm.max_restarts not in values:
            self.cb_restarts.configure(values=values + [fm.max_restarts])
        self.v_restarts.set(fm.max_restarts)
        self.v_no_progress.set(fm.max_no_progress)
        self.v_attempts.set(fm.max_frame_attempts)

    def _save_task(self, cfg, blender):
        """点击开始后**先存档再跑**：只有这样，进程被强杀时才有东西可供开机续跑。"""
        try:
            p = taskstore.save(cfg, blender, autoresume=True)
            self._log("任务已存档：%s（意外中断后开机可自动续跑）" % p)
        except Exception as e:
            # 存不上只损失"自动续跑"，不该拦住建档本身，但必须在日志里说清楚
            self._log("! 任务存档失败（不影响本次渲染，但开机不会自动续跑）：%s: %s"
                      % (type(e).__name__, e))

    def try_autostart_resume(self):
        """开机自启拉起时的入口：认领未完成任务。"""
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
            self._log("  上次是你主动停止的，所以**不自动跑** —— 确认参数后点「开始渲染」继续")
            return
        self._log("检测到未完成任务，自动续跑：%s" % taskstore.describe(rec))
        self.root.after(300, self.on_start)

    def _collect_form(self):
        self.form.blend = self.v_blend.get()
        self.form.scene = self.v_scene.get()
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

        bad = self._scene_conflict()
        if bad:
            messagebox.showerror("配置有问题", bad)
            return

        # 存档要**赶在起线程之前**：晚一步就意味着"已经开始跑但还没存档"这段窗口里
        # 被强杀会让这次任务无从续起
        self._save_task(cfg, blender)
        self.job = RenderJob(cfg, blender, cmd_factory=self.cmd_factory)
        self.stopping = False
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.pb["value"] = 0
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self._log("开始渲染：%s%s（%d 帧）"
                  % (cfg.blend, "（场景 %s）" % cfg.scene if cfg.scene else "", len(cfg.frames)))

    def _scene_conflict(self):
        """选了场景、但该工程里没这个名字 → 拦下来。

        为什么要在界面拦：命令行 `-S 不存在的场景` 时 Blender **只打一行英文提示就
        照常渲染默认场景、退出码还是 0**（实测 5.2.2），不拦就会静默渲出一整套错图。
        """
        scene = scene_to_config(self.v_scene.get())
        info = self._scene_info
        if not scene or not info:
            return ""
        if os.path.abspath(info.get("blend") or "") != os.path.abspath(self.v_blend.get().strip()):
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
        self.btn_stop.configure(state="disabled")
        self._log("正在停止…（已完成的部分已保存，下次可从断点继续）")
        # 主动停止 = "我知道，别自己跑"：撤掉自动续跑标记，但断点文件原样留着。
        # 改标记而不是删存档 —— 删了断点就成了"没跑过"，用户还得从头来。
        if taskstore.set_autoresume(False):
            self._log("已取消开机自动续跑（断点保留，想继续随时点「开始渲染」）")

    def on_close(self):
        if self.job is not None and self.prog.running:
            if not messagebox.askokcancel("任务还在跑", "停止并退出？进度已保存。",
                                          parent=self.root):
                return
            self.job.cancel()
            # 关窗退出等同于「主动停止」：撤掉自动续跑，别下次开机偷跑
            taskstore.set_autoresume(False)
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
            if kind == "job_done":
                self._settle_task(ev)

    def _settle_task(self, ev):
        """一轮跑完后处理待办存档。

        这里的分支和「崩溃时来不及写文件」是配套的：**只有明确知道结果时才动存档**。
        - 渲完 → 删档，否则下次开机会白跑一次（虽然会立刻发现已完成，但仍是噪音）
        - 被取消 → 撤掉自动续跑（用户点的停止，别开机偷跑）
        - 其它失败（额度耗尽/无进展/异常）→ **原样保留**，下次开机再试一次；
          这些失败可能是关机导致的，重试一次的成本远低于"任务丢了"
        """
        if ev.get("ok"):
            if taskstore.clear():
                self._log("全部帧渲完，已清理待办存档（下次开机不会再自动跑）")
        elif ev.get("cancelled"):
            taskstore.set_autoresume(False)

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


def run_gui(demo=False, cmd_factory=None, preset_blend=None, autostart_mode=False):
    """构造并启动界面（阻塞直到窗口关闭）。"""
    root = tk.Tk()
    theme.apply(root)                           # 深色主题要在建控件之前套上
    try:
        root.tk.call("tk", "scaling", 1.3)      # 高 DPI 下别糊
    except Exception:
        pass
    App(root, demo=demo, cmd_factory=cmd_factory, preset_blend=preset_blend,
        autostart_mode=autostart_mode)
    root.mainloop()
    return 0
