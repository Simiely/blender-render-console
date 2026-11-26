# -*- coding: utf-8 -*-
"""最简 GUI —— 只验证 core 渲染流程是否正常，不依赖 guimodel/gui/view_contract。

    python min_gui.py                 # 打开界面
    python min_gui.py 工程.blend       # 打开界面并读取该工程的配置

目的：当完整界面（8 个控制器 + 视图契约）出问题时，用这个最小窗口判断
"是 core 坏了，还是 GUI 层坏了"。流程：选工程 → 读配置回填 → 改参数 → 渲染 → 看日志。

读取配置和渲染都跑在后台线程，结果经事件队列回主线程填控件（tkinter 不是线程安全的）。
不写任何存档，不碰开机自启。
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from brconsole import tkboot  # noqa: E402

# 本机 Python 无 tkinter —— 必须先用 sidecar 自举重启，再 import 任何 GUI 模块
if not tkboot.bootstrap(ROOT, list(sys.argv[1:])) and not tkboot.has_tkinter():
    sys.stderr.write("缺 tkinter 且无 sidecar。先跑：python tools/build_tkinter.py\n")
    sys.exit(2)

from brconsole.core import JobConfig, RenderJob  # noqa: E402
from brconsole.guimodel.events import event_text  # noqa: E402
from brconsole.inspect import (read_blend_info, scene_detail, scene_names,  # noqa: E402
                               summarize, output_template_from)
from brconsole.locate import resolve_blender  # noqa: E402

ENGINES = ("CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH")


class MinGUI(object):
    def __init__(self, root, preset=None):
        self.root = root
        root.title("brc 最简测试界面")
        root.geometry("760x640")
        root.minsize(640, 500)

        f = ttk.Frame(root, padding=(10, 8))
        f.pack(fill="both", expand=True)

        row = 0
        # --- 工程 + 读取配置 ---
        ttk.Label(f, text="工程 .blend").grid(row=row, column=0, sticky="w")
        self.blend = tk.StringVar(value=preset or "")
        ttk.Entry(f, textvariable=self.blend, width=66).grid(row=row, column=1, sticky="ew")
        ttk.Button(f, text="选择", command=self._pick).grid(row=row, column=2)
        self.btn_read = ttk.Button(f, text="读取配置", command=self.on_read)
        self.btn_read.grid(row=row, column=3, padx=(6, 0))
        row += 1

        # --- 场景（读回来后可切换）---
        ttk.Label(f, text="场景").grid(row=row, column=0, sticky="w")
        self.scene = tk.StringVar()
        self.scene_box = ttk.Combobox(f, textvariable=self.scene, width=28,
                                      state="readonly")
        self.scene_box.grid(row=row, column=1, sticky="w")
        self.scene_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_scene())
        row += 1

        # --- 帧范围 / 引擎 / 采样 ---
        ttk.Label(f, text="帧范围").grid(row=row, column=0, sticky="w")
        rowf = ttk.Frame(f)
        rowf.grid(row=row, column=1, columnspan=3, sticky="w")
        self.start = tk.StringVar(value="1")
        self.end = tk.StringVar(value="3")
        ttk.Entry(rowf, textvariable=self.start, width=9).pack(side="left")
        ttk.Label(rowf, text=" ~ ").pack(side="left")
        ttk.Entry(rowf, textvariable=self.end, width=9).pack(side="left")
        ttk.Label(rowf, text="   引擎 ").pack(side="left", padx=(14, 0))
        self.engine = tk.StringVar(value="CYCLES")
        ttk.Combobox(rowf, textvariable=self.engine, values=ENGINES,
                     width=18, state="readonly").pack(side="left")
        ttk.Label(rowf, text="   采样 ").pack(side="left", padx=(14, 0))
        self.samples = tk.StringVar(value="64")
        ttk.Entry(rowf, textvariable=self.samples, width=8).pack(side="left")
        row += 1

        # --- 分辨率 ---
        ttk.Label(f, text="分辨率").grid(row=row, column=0, sticky="w")
        resf = ttk.Frame(f)
        resf.grid(row=row, column=1, columnspan=3, sticky="w")
        self.width = tk.StringVar(value="")
        self.height = tk.StringVar(value="")
        ttk.Entry(resf, textvariable=self.width, width=9).pack(side="left")
        ttk.Label(resf, text=" x ").pack(side="left")
        ttk.Entry(resf, textvariable=self.height, width=9).pack(side="left")
        ttk.Label(resf, text="  （留空＝用工程设置）").pack(side="left", padx=(8, 0))
        row += 1

        # --- 输出模板 ---
        ttk.Label(f, text="输出模板").grid(row=row, column=0, sticky="w")
        self.output = tk.StringVar()
        ttk.Entry(f, textvariable=self.output, width=66).grid(row=row, column=1, sticky="ew")
        row += 1

        # --- Blender ---
        ttk.Label(f, text="Blender").grid(row=row, column=0, sticky="w")
        self.blender = tk.StringVar()
        path, _cands, _why = resolve_blender(None)
        if path:
            self.blender.set(path)
        ttk.Entry(f, textvariable=self.blender, width=66).grid(row=row, column=1, sticky="ew")
        row += 1

        # --- 按钮 ---
        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=4, pady=8, sticky="w")
        self.btn_start = ttk.Button(btns, text="开始渲染", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        row += 1

        self.status = tk.StringVar(value="就绪")
        ttk.Label(f, textvariable=self.status, foreground="#444").grid(
            row=row, column=0, columnspan=4, sticky="w")
        row += 1

        self.log = tk.Text(f, height=16, wrap="word")
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.log.yview)
        sb.grid(row=row, column=3, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(row, weight=1)

        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.running = False
        self.reading = False
        self.info = None                  # 已读到的工程信息（切场景不重读 Blender）
        root.after(80, self._poll)
        if preset:
            root.after(200, self.on_read)

    # ---------------- 控件动作 ----------------
    def _pick(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(filetypes=[("Blender 工程", "*.blend")])
        if p:
            self.blend.set(p)

    def _say(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def on_read(self):
        """读工程配置：后台跑 inspect，结果经 q 回主线程填控件。"""
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
        self._say("读取工程配置：%s（约几秒，大工程更久）" % blend)

        def work():
            try:
                info = read_blend_info(blender, blend)
                self.q.put(("inspect_done", info))
            except Exception as exc:
                self.q.put(("inspect_error", {"error": "%s: %s" % (type(exc).__name__, exc)}))

        threading.Thread(target=work, daemon=True).start()

    def _apply_scene(self):
        """切换场景：从已缓存的 info 里取该场景详情，不重起 Blender。"""
        detail = scene_detail(self.info, self.scene.get())
        if detail:
            self._fill(detail)

    def _fill(self, detail):
        """把 inspect 的一个场景详情填进控件。字段缺失就不动控件（宁可不改，不写错值）。"""
        if detail.get("frame_start") is not None:
            self.start.set(str(detail["frame_start"]))
        if detail.get("frame_end") is not None:
            self.end.set(str(detail["frame_end"]))
        engine = str(detail.get("engine") or "").upper()
        if engine == "BLENDER_EEVEE_NEXT":       # 4.2+ 的别名，界面只认 BLENDER_EEVEE
            engine = "BLENDER_EEVEE"
        if engine in ENGINES:
            self.engine.set(engine)
        if detail.get("samples") is not None:
            self.samples.set(str(detail["samples"]))
        res = detail.get("resolution") or []
        if len(res) == 2:
            self.width.set(str(res[0]))
            self.height.set(str(res[1]))
        blend = self.blend.get().strip()
        tpl = output_template_from(detail.get("output_path"), blend)
        if tpl:
            self.output.set(tpl)

    def on_start(self):
        if self.running:
            return
        blend = self.blend.get().strip()
        blender = self.blender.get().strip()
        if not blend or not os.path.exists(blend):
            self._say("! 先选一个存在的 .blend 工程")
            return
        if not blender or not os.path.exists(blender):
            self._say("! blender.exe 不存在：%s" % blender)
            return
        try:
            s, e = int(self.start.get()), int(self.end.get())
        except ValueError:
            self._say("! 帧范围要是整数")
            return
        samples = self.samples.get().strip()
        res = None
        if self.width.get().strip() and self.height.get().strip():
            res = (int(self.width.get()), int(self.height.get()))
        out = self.output.get().strip() or output_template_from("", blend)
        cfg = JobConfig(blend, range(s, e + 1), out,
                        engine=self.engine.get(),
                        samples=int(samples) if samples.isdigit() else None,
                        resolution=res,
                        scene=self.scene.get() or None,
                        max_restarts=3, max_frame_attempts=2)
        self.job = RenderJob(cfg, blender)
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status.set("渲染中…")
        self._say("开始：%s，场景 %s，帧 %s~%s，输出 %s" % (
            blend, self.scene.get() or "（默认）", s, e, out))

        def work():
            try:
                self.job.run(on_event=lambda k, v: self.q.put((k, v)))
            except Exception as exc:                  # core 按约定不抛，这里是兜底
                self.q.put(("fatal", {"error": "%s: %s" % (type(exc).__name__, exc)}))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def on_stop(self):
        if self.job:
            self.job.cancel()
            self.status.set("正在停止…")

    # ---------------- 事件泵（tkinter 只能在主线程碰控件）----------------
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
                self.status.set("读取失败")
                self._say("! 读取工程配置出错：%s" % ev.get("error"))
                continue
            text = event_text(kind, ev)
            if kind == "frame_done":
                self.status.set("已完成帧 %s（%.2fs）" % (ev.get("frame"), ev.get("secs") or 0))
            elif kind in ("job_done", "job_error", "fatal"):
                self._finish(kind, ev)
            if text:
                self._say(text)
            elif kind == "native" and ev.get("line"):
                line = ev["line"]
                # Blender 启动横幅/退出噪音不显示（ Saved / Fra / Sample 等有效行照常）
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
        if active and active in names:
            self.scene.set(active)
        detail = scene_detail(info, active)
        if detail:
            self._fill(detail)
        self.status.set("已读取工程配置")
        self._say(summarize(info))

    def _finish(self, kind, ev):
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if kind == "job_done" and ev.get("ok"):
            # 事实文案交给 event_text（统一口径，别在这里再拼一条 0/0 的错数）
            self.status.set("完成 %s/%s 帧" % (
                len(ev.get("done") or []), ev.get("total") or 0))
        else:
            why = ev.get("error") or ev.get("msg") or kind
            self.status.set("失败：%s" % why)
            self._say("任务失败：%s" % why)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    preset = argv[0] if argv and argv[0].endswith(".blend") else None
    root = tk.Tk()
    MinGUI(root, preset)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
