# -*- coding: utf-8 -*-
"""最简 GUI —— 只验证 core 渲染流程是否正常，不依赖 guimodel/gui/view_contract。

    python min_gui.py                 # 打开界面
    python min_gui.py 工程.blend       # 预填工程

目的：当完整界面（8 个控制器 + 视图契约）出问题时，用这个最小窗口判断
"是 core 坏了，还是 GUI 层坏了"。它只做四件事：选工程 → 设帧范围 → 渲染 → 显示日志。

无第三方依赖（除 tkinter，本机靠 sidecar 自举）；不写任何存档，不碰开机自启。
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
from brconsole.locate import resolve_blender  # noqa: E402


class MinGUI(object):
    def __init__(self, root, preset=None):
        self.root = root
        root.title("brc 最简测试界面")
        root.geometry("720x560")
        root.minsize(600, 460)

        f = ttk.Frame(root, padding=10)
        f.pack(fill="both", expand=True)

        row = 0
        ttk.Label(f, text="工程 .blend").grid(row=row, column=0, sticky="w")
        self.blend = tk.StringVar(value=preset or "")
        ttk.Entry(f, textvariable=self.blend, width=62).grid(row=row, column=1, sticky="ew")
        ttk.Button(f, text="选择", command=self._pick).grid(row=row, column=2)
        row += 1

        ttk.Label(f, text="帧范围").grid(row=row, column=0, sticky="w")
        rowf = ttk.Frame(f)
        rowf.grid(row=row, column=1, columnspan=2, sticky="w")
        self.start = tk.StringVar(value="1")
        self.end = tk.StringVar(value="3")
        ttk.Entry(rowf, textvariable=self.start, width=8).pack(side="left")
        ttk.Label(rowf, text=" ~ ").pack(side="left")
        ttk.Entry(rowf, textvariable=self.end, width=8).pack(side="left")
        row += 1

        ttk.Label(f, text="Blender").grid(row=row, column=0, sticky="w")
        self.blender = tk.StringVar()
        path, _cands, _why = resolve_blender(None)
        if path:
            self.blender.set(path)
        ttk.Entry(f, textvariable=self.blender, width=62).grid(row=row, column=1, sticky="ew")
        row += 1

        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=3, pady=8, sticky="w")
        self.btn_start = ttk.Button(btns, text="开始渲染", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        row += 1

        self.status = tk.StringVar(value="就绪")
        ttk.Label(f, textvariable=self.status, foreground="#444").grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1

        self.log = tk.Text(f, height=18, wrap="word")
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
        root.after(80, self._poll)

    # ---------------- 控件动作 ----------------
    def _pick(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(filetypes=[("Blender 工程", "*.blend")])
        if p:
            self.blend.set(p)

    def _say(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

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
        out = os.path.join(os.path.dirname(blend), "min_####")
        cfg = JobConfig(blend, range(s, e + 1), out, samples=8,
                        max_restarts=3, max_frame_attempts=2)
        self.job = RenderJob(cfg, blender)
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status.set("渲染中…")
        self._say("开始：%s，帧 %s~%s，输出到 %s" % (blend, s, e, out))

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
