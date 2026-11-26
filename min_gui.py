# -*- coding: utf-8 -*-
"""最简 GUI —— 功能对齐完整界面，但不依赖 guimodel/gui/view_contract。

    python min_gui.py                 # 打开界面
    python min_gui.py 工程.blend       # 打开并自动读取该工程配置

目的：完整界面出问题时，用它判断是 core 坏了还是 GUI 层坏了。
所有控件规则（引擎/格式/位数/重试/善后）复用 brconsole.guimodel 的唯一出口，
读取配置复用 brconsole.inspect，不重写 bpy 逻辑；UI 只管摆控件和转发事件。

读取与渲染都在后台线程，结果经事件队列回主线程填控件（tkinter 不是线程安全的）。
"""

import os
import queue
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from brconsole import tkboot  # noqa: E402

# ⚠️ 顺序不能反：打包后 exe 里的 Python 也没有 tkinter，必须先把它自带的
#    `_brc/{DLLs,Lib/tkinter,tcl}` 挂回运行环境，再 import tkinter。
#    源码方式则靠 bootstrap 带 sidecar 重启自己一次。
tkboot.apply_frozen_env()
if not tkboot.bootstrap(ROOT, list(sys.argv[1:])) and not tkboot.has_tkinter():
    sys.stderr.write("缺 tkinter 且无 sidecar。先跑：python tools/build_tkinter.py\n")
    sys.exit(2)

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from brconsole import autostart, notify, power  # noqa: E402
from brconsole.core import RenderJob, state_file_for  # noqa: E402
from brconsole.eta import fmt_duration  # noqa: E402
from brconsole.guimodel.constants import (AFTERMATH_LABELS, DEVICES, DEPTH_KEEP_LABEL,  # noqa: E402
                                          ENGINES, FORMATS, RETRY_CHOICES)
from brconsole.guimodel.events import aftermath_plan, event_text  # noqa: E402
from brconsole.guimodel.form import depth_choices, format_label  # noqa: E402
from brconsole.inspect import read_blend_info, scene_detail, scene_names, summarize  # noqa: E402
from brconsole.locate import find_blender, resolve_blender  # noqa: E402
import min_gui_model as model  # noqa: E402


class MinGUI(object):
    def __init__(self, root, preset=None):
        self.root = root
        root.title("brc 最简测试界面（功能对齐）")
        root.geometry("860x820")
        root.minsize(760, 640)

        # 一个滚动画布装所有控件——功能多但不管视觉，宁可长也不要挤
        outer = ttk.Frame(root)
        outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, highlightthickness=0)
        sb0 = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb0.set)
        sb0.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        f = ttk.Frame(cv, padding=8)
        cv.create_window((0, 0), window=f, anchor="nw")
        f.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        r = 0

        def label(text):
            nonlocal r
            ttk.Label(f, text=text, width=12, anchor="w").grid(row=r, column=0, sticky="w")
            return r

        # --- 工程 / 场景 / 读取 ---
        label("工程 .blend")
        self.blend = tk.StringVar(value=preset or "")
        ttk.Entry(f, textvariable=self.blend, width=64).grid(row=r, column=1, sticky="ew")
        ttk.Button(f, text="选择", command=self._pick).grid(row=r, column=2)
        self.btn_read = ttk.Button(f, text="读取配置", command=self.on_read)
        self.btn_read.grid(row=r, column=3)
        r += 1

        label("场景")
        self.scene = tk.StringVar()
        self.scene_box = ttk.Combobox(f, textvariable=self.scene, width=30, state="readonly")
        self.scene_box.grid(row=r, column=1, sticky="w")
        self.scene_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_scene())
        r += 1

        label("集合显示")
        self.coll_frame = ttk.Frame(f)
        self.coll_frame.grid(row=r, column=1, columnspan=3, sticky="w")
        self.coll_vars = {}
        r += 1

        # --- 帧范围 / 多段 ---
        label("帧范围")
        rowf = ttk.Frame(f)
        rowf.grid(row=r, column=1, columnspan=3, sticky="w")
        self.start = tk.StringVar(value="1")
        self.end = tk.StringVar(value="3")
        ttk.Entry(rowf, textvariable=self.start, width=8).pack(side="left")
        ttk.Label(rowf, text=" ~ ").pack(side="left")
        ttk.Entry(rowf, textvariable=self.end, width=8).pack(side="left")
        ttk.Label(rowf, text="  帧列表(优先) ").pack(side="left", padx=(10, 0))
        self.frames_spec = tk.StringVar()
        ttk.Entry(rowf, textvariable=self.frames_spec, width=28).pack(side="left")
        ttk.Label(rowf, text="例 1-10,15,20-25x2").pack(side="left")
        r += 1

        # --- 引擎 / 设备 / 采样 ---
        label("引擎 / 设备")
        row2 = ttk.Frame(f)
        row2.grid(row=r, column=1, columnspan=3, sticky="w")
        self.engine = tk.StringVar(value="CYCLES")
        ttk.Combobox(row2, textvariable=self.engine, width=22, state="readonly",
                     values=[v for _k, v in ENGINES]).pack(side="left")
        self.device = tk.StringVar(value="")
        ttk.Combobox(row2, textvariable=self.device, width=14, state="readonly",
                     values=[v for _k, v in DEVICES]).pack(side="left", padx=(8, 0))
        ttk.Label(row2, text="  采样 ").pack(side="left", padx=(8, 0))
        self.samples = tk.StringVar(value="64")
        ttk.Entry(row2, textvariable=self.samples, width=8).pack(side="left")
        r += 1

        # --- 分辨率 / 百分比 / 格式 / 位数 ---
        label("分辨率/格式")
        row3 = ttk.Frame(f)
        row3.grid(row=r, column=1, columnspan=3, sticky="w")
        self.width, self.height, self.pct = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Entry(row3, textvariable=self.width, width=7).pack(side="left")
        ttk.Label(row3, text="x").pack(side="left")
        ttk.Entry(row3, textvariable=self.height, width=7).pack(side="left")
        ttk.Label(row3, text=" % ").pack(side="left")
        ttk.Entry(row3, textvariable=self.pct, width=6).pack(side="left")
        ttk.Label(row3, text="  格式 ").pack(side="left", padx=(8, 0))
        self.fmt = tk.StringVar(value=format_label(""))
        self.fmt_box = ttk.Combobox(row3, textvariable=self.fmt, width=24, state="readonly",
                                    values=[format_label(s[0]) for s in FORMATS])
        self.fmt_box.pack(side="left")
        self.fmt_box.bind("<<ComboboxSelected>>", lambda _e: self._on_fmt())
        ttk.Label(row3, text=" 位数 ").pack(side="left", padx=(8, 0))
        self.depth = tk.StringVar(value=DEPTH_KEEP_LABEL)
        self.depth_box = ttk.Combobox(row3, textvariable=self.depth, width=8, state="disabled")
        self.depth_box.pack(side="left")
        r += 1

        # --- 输出 / Blender ---
        label("输出模板")
        self.output = tk.StringVar()
        ttk.Entry(f, textvariable=self.output, width=64).grid(row=r, column=1, sticky="ew")
        ttk.Button(f, text="选择目录", command=self._choose_dir).grid(row=r, column=2)
        r += 1

        label("Blender")
        row4 = ttk.Frame(f)
        row4.grid(row=r, column=1, columnspan=3, sticky="w")
        self.blender = tk.StringVar()
        p, _c, _w = resolve_blender(None)
        if p:
            self.blender.set(p)
        ttk.Entry(row4, textvariable=self.blender, width=54).pack(side="left")
        self.ver = tk.StringVar()
        self.ver_box = ttk.Combobox(row4, textvariable=self.ver, width=26, state="readonly")
        self.ver_box.pack(side="left", padx=(6, 0))
        self.ver_box.bind("<<ComboboxSelected>>",
                          lambda _e: self.blender.set(self.ver.get()))
        ttk.Button(row4, text="探测", command=self._detect).pack(side="left", padx=(6, 0))
        ttk.Button(row4, text="深扫", command=lambda: self._detect(deep=True)).pack(side="left")
        r += 1

        # --- 重试三参数 ---
        label("重试")
        row5 = ttk.Frame(f)
        row5.grid(row=r, column=1, columnspan=3, sticky="w")
        self.retry = tk.StringVar(value=RETRY_CHOICES[0])
        ttk.Combobox(row5, textvariable=self.retry, width=24, state="readonly",
                     values=RETRY_CHOICES).pack(side="left")
        ttk.Label(row5, text=" 覆盖: 重启 ").pack(side="left", padx=(8, 0))
        self.restarts_ov = tk.StringVar()
        ttk.Entry(row5, textvariable=self.restarts_ov, width=6).pack(side="left")
        ttk.Label(row5, text=" 单帧 ").pack(side="left")
        self.attempts_ov = tk.StringVar()
        ttk.Entry(row5, textvariable=self.attempts_ov, width=6).pack(side="left")
        ttk.Label(row5, text=" 无进展 ").pack(side="left")
        self.noprogress_ov = tk.StringVar()
        ttk.Entry(row5, textvariable=self.noprogress_ov, width=6).pack(side="left")
        r += 1

        # --- 开关 ---
        label("开关")
        row6 = ttk.Frame(f)
        row6.grid(row=r, column=1, columnspan=3, sticky="w")
        self.resume = tk.BooleanVar(value=True)
        self.native = tk.BooleanVar(value=True)
        self.sleep_guard = tk.BooleanVar(value=True)
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(row6, text="断点续跑", variable=self.resume).pack(side="left")
        ttk.Checkbutton(row6, text="原生输出", variable=self.native).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(row6, text="防睡眠", variable=self.sleep_guard).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(row6, text="开机自启", variable=self.autostart_var,
                        command=self._toggle_autostart).pack(side="left", padx=(6, 0))
        ttk.Label(row6, text=" 结束后 ").pack(side="left", padx=(8, 0))
        self.aftermath = tk.StringVar(value=AFTERMATH_LABELS[0][0])
        ttk.Combobox(row6, textvariable=self.aftermath, width=16, state="readonly",
                     values=[lab for lab, _v in AFTERMATH_LABELS]).pack(side="left")
        r += 1

        # --- 按钮 ---
        label("操作")
        row7 = ttk.Frame(f)
        row7.grid(row=r, column=1, columnspan=3, sticky="w", pady=6)
        self.btn_start = ttk.Button(row7, text="开始渲染", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row7, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        ttk.Button(row7, text="清空断点", command=self._clear_state).pack(side="left", padx=4)
        ttk.Button(row7, text="打开输出目录", command=self._open_outdir).pack(side="left", padx=4)
        ttk.Button(row7, text="清空日志", command=self._clear_log).pack(side="left", padx=4)
        r += 1

        # --- 进度 ---
        label("进度")
        row8 = ttk.Frame(f)
        row8.grid(row=r, column=1, columnspan=3, sticky="ew")
        self.pb = ttk.Progressbar(row8, mode="determinate", maximum=100)
        self.pb.pack(side="left", fill="x", expand=True)
        self.pb_label = tk.StringVar(value="0/0")
        ttk.Label(row8, textvariable=self.pb_label, width=12).pack(side="left", padx=(6, 0))
        self.eta_label = tk.StringVar(value="ETA --")
        ttk.Label(row8, textvariable=self.eta_label, width=24).pack(side="left", padx=(6, 0))
        r += 1

        label("状态")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(f, textvariable=self.status, anchor="w").grid(row=r, column=1, sticky="w")
        r += 1

        # --- 日志 ---
        self.log = tk.Text(f, height=14, wrap="word")
        self.log.grid(row=r, column=1, columnspan=3, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.log.yview)
        sb.grid(row=r, column=3, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(r, weight=1)

        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.running = False
        self.reading = False
        self.info = None
        self.last_result = None
        self._on_fmt()
        self._refresh_versions()
        root.after(80, self._poll)
        if preset:
            root.after(200, self.on_read)

    # ---------------- 控件动作 ----------------
    def _pick(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(filetypes=[("Blender 工程", "*.blend")])
        if p:
            self.blend.set(p)

    def _choose_dir(self):
        from tkinter import filedialog
        tpl = self.output.get().strip()
        d = os.path.dirname(tpl) if tpl else ""
        p = filedialog.askdirectory(initialdir=d or None)
        if p:
            self.output.set(os.path.join(p, "frame_####"))

    def _say(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _clear_log(self):
        self.log.delete("1.0", "end")

    def _detect(self, deep=False):
        cands = find_blender(deep=deep)
        if not cands:
            self._say("! 没探测到 blender.exe")
            return
        self.ver_box.configure(values=cands)
        self.ver.set(cands[0])
        self.blender.set(cands[0])
        self._say("探测到 %d 个 Blender：%s" % (len(cands), cands[0]))

    def _refresh_versions(self):
        cands = find_blender(deep=False)
        if cands:
            self.ver_box.configure(values=cands)
            if not self.ver.get():
                self.ver.set(cands[0])

    def _toggle_autostart(self):
        try:
            autostart.sync(bool(self.autostart_var.get()))
            self._say("开机自启已%s（登记命令：%s）" % (
                "开启" if self.autostart_var.get() else "关闭", autostart.launch_command()))
        except Exception as exc:
            self._say("! 自启开关失败：%s: %s" % (type(exc).__name__, exc))

    def _on_fmt(self):
        ident = self.fmt.get().strip().upper() or ""
        choices = depth_choices(ident)
        self.depth_box.configure(values=choices,
                                 state="readonly" if len(choices) > 1 else "disabled")
        if self.depth.get() not in choices:
            self.depth.set(choices[0])

    def _clear_state(self):
        if self.running:
            self._say("! 渲染中，不能清断点")
            return
        tpl = self.output.get().strip() or model.output_template_from("", self.blend.get())
        p = state_file_for(tpl, None)
        if os.path.exists(p):
            os.remove(p)
            self._say("已清空断点：%s" % p)
        else:
            self._say("没有断点文件：%s" % p)

    def _open_outdir(self):
        tpl = self.output.get().strip()
        d = os.path.dirname(os.path.abspath(tpl)) if tpl else ""
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            self._say("输出目录还不存在：%s" % d)

    # ---------------- 读配置 ----------------
    def on_read(self):
        if self.reading:
            return
        blend = self.blend.get().strip()
        blender = self.blender.get().strip()
        if not blend or not os.path.exists(blend):
            self._say("! 先选一个存在的 .blend 工程")
            return
        if not blender or not os.path.exists(blender):
            self._say("! blender.exe 不存在：%s" % blender)
            return
        self.reading = True
        self.btn_read.configure(state="disabled")
        self.status.set("读取工程配置中…")

        def work():
            try:
                self.q.put(("inspect_done", read_blend_info(blender, blend)))
            except Exception as exc:
                self.q.put(("inspect_error", {"error": "%s: %s" % (type(exc).__name__, exc)}))

        threading.Thread(target=work, daemon=True).start()

    def _values(self):
        return {
            "blend": self.blend.get(), "blender": self.blender.get(),
            "scene": self.scene.get(),
            "frames_spec": self.frames_spec.get(),
            "start": self.start.get(), "end": self.end.get(),
            "engine": self.engine.get(), "device": self.device.get(),
            "samples": self.samples.get(), "width": self.width.get(),
            "height": self.height.get(), "pct": self.pct.get(),
            "format": self.fmt.get(), "depth": self.depth.get(),
            "output": self.output.get(), "retry": self.retry.get(),
            "restarts_override": self.restarts_ov.get(),
            "attempts_override": self.attempts_ov.get(),
            "no_progress_override": self.noprogress_ov.get(),
            "resume": bool(self.resume.get()),
            "hidden": self._hidden(),
        }

    def _hidden(self):
        """界面勾选＝显示；hidden_collections 存的是要隐藏的名字（只在这一层反转）。"""
        return [name for name, var in self.coll_vars.items() if not var.get()]

    def _fill_detail(self, detail):
        vals = model.fill_from_detail(self._values(), detail, self.blend.get().strip())
        self.start.set(vals["start"])
        self.end.set(vals["end"])
        self.engine.set(vals["engine"])
        self.device.set(vals["device"])
        self.samples.set(vals["samples"])
        self.width.set(vals["width"])
        self.height.set(vals["height"])
        self.pct.set(vals.get("pct", ""))
        self.fmt.set(vals["format"])
        self._on_fmt()
        if vals["depth"] in (self.depth_box.cget("values") or ()):
            self.depth.set(vals["depth"])
        self.output.set(vals["output"])

    def _apply_scene(self):
        detail = scene_detail(self.info, self.scene.get())
        if detail:
            self._fill_detail(detail)

    # ---------------- 渲染 ----------------
    def on_start(self):
        if self.running:
            return
        cfg, err = model.build_config(self._values())
        if err:
            self._say("! %s" % err)
            return
        level, why = model.check_disk(cfg)
        if level == "block":
            self._say("! 磁盘空间不足，不开始：%s" % why)
            return
        if level == "warn":
            self._say("! %s" % why)

        guard = (power.keep_awake, power.allow_sleep) if self.sleep_guard.get() else None
        self.job = RenderJob(cfg, self.blender.get().strip(), power_guard=guard)
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.pb.configure(maximum=max(len(cfg.frames), 1), value=0)
        self.status.set("渲染中…")
        self._say("开始：%s · 场景 %s · %d 帧" % (
            cfg.blend, cfg.scene or "默认", len(cfg.frames)))

        def work():
            try:
                self.job.run(on_event=lambda k, v: self.q.put((k, v)))
            except Exception as exc:
                self.q.put(("fatal", {"error": "%s: %s" % (type(exc).__name__, exc)}))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def on_stop(self):
        if self.job:
            self.job.cancel()
            self.status.set("正在停止…")

    # ---------------- 事件泵 ----------------
    def _poll(self):
        while True:
            try:
                kind, ev = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "inspect_done":
                self._on_inspect(ev)
                continue
            if kind == "inspect_error":
                self.reading = False
                self.btn_read.configure(state="normal")
                self._say("! 读取失败：%s" % ev.get("error"))
                continue
            text = event_text(kind, ev)
            if kind == "frame_done":
                done, total = ev.get("done") or 0, ev.get("total") or 0
                self.pb.configure(value=done)
                self.pb_label.set("%s/%s" % (done, total))
                eta = ev.get("eta_sec")
                mode = ev.get("eta_mode") or ""
                self.eta_label.set(("ETA %s（%s）" % (fmt_duration(eta), mode))
                                   if eta is not None else "ETA --")
            elif kind in ("job_done", "job_error", "fatal"):
                self._finish(kind, ev)
            if text:
                self._say(text)
            elif kind == "native" and ev.get("line") and self.native.get():
                line = ev["line"]
                if "Blender quit" in line or line.startswith("Blender 5."):
                    continue
                self._say(line)
        self.root.after(80, self._poll)

    def _on_inspect(self, info):
        self.reading = False
        self.btn_read.configure(state="normal")
        if not info or not info.get("ok"):
            self.status.set("读取失败")
            self._say("! %s" % ((info or {}).get("error") or "未知错误"))
            return
        self.info = info
        names = scene_names(info)
        self.scene_box.configure(values=names)
        active = str(info.get("scene") or "")
        if active in names:
            self.scene.set(active)
        detail = scene_detail(info, active)
        if detail:
            self._fill_detail(detail)
        # 集合显示：勾选＝显示；工程里 hide_render=True 的默认不勾
        for w in list(self.coll_frame.winfo_children()):
            w.destroy()
        self.coll_vars = {}
        for c in (info.get("collections") or []):
            name = str(c.get("name") or "")
            v = tk.BooleanVar(value=not bool(c.get("hidden")))
            self.coll_vars[name] = v
            ttk.Checkbutton(self.coll_frame, text="%s (%s)" % (name, c.get("objects", 0)),
                            variable=v).pack(side="left", padx=(0, 8))
        self.status.set("已读取工程配置")
        self._say(summarize(info))

    def _finish(self, kind, ev):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.last_result = ev
        ok = kind == "job_done" and bool(ev.get("ok"))
        cancelled = bool(ev.get("cancelled"))
        if ok:
            self.status.set("完成 %s/%s 帧" % (
                len(ev.get("done") or []), ev.get("total") or 0))
        else:
            self.status.set("失败：%s" % (ev.get("error") or ev.get("msg") or kind))

        # 完成通知（尽力而为，失败不影响主流程）
        try:
            notify.announce(tk_winfo_id=self.root.winfo_id(),
                            title="渲染完成" if ok else "渲染失败",
                            message=self.status.get())
        except Exception:
            pass

        # 善后：只有"全部渲完"才触发；关机带 60 秒倒计时可撤
        choice = dict(AFTERMATH_LABELS).get(self.aftermath.get())
        act = aftermath_plan(choice, ok, cancelled)
        if act == "shutdown":
            ok2, msg = power.shutdown_after()
            self._say("善后：%s" % msg)
        elif act == "sleep":
            self._say("善后：即将睡眠")
            power.sleep_now()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    preset = argv[0] if argv and argv[0].endswith(".blend") else None
    root = tk.Tk()
    MinGUI(root, preset)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
