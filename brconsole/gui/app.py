# -*- coding: utf-8 -*-
"""主窗口薄壳：装配状态、视图与控制器 mixin，保留事件循环入口。
"""

import os
import sys
import tkinter as tk
from tkinter import messagebox

from .. import applock, queue_store, theme
from ..gui_view_new import TkView
from ..guimodel.form import FormModel
from ..guimodel.frames import new_segment
from .common import POLL_MS, WINDOW_TITLE, _apply_icon
from .app_state import AppState
from .form_controller import FormControllerMixin
from .inspect_controller import InspectControllerMixin
from .queue_controller import QueueControllerMixin
from .render_controller import RenderControllerMixin
from .log_controller import LogControllerMixin
from .event_controller import EventControllerMixin
from .aftermath_controller import AftermathControllerMixin
from .refresh_controller import RefreshControllerMixin

class App(FormControllerMixin, InspectControllerMixin,
          QueueControllerMixin, RenderControllerMixin,
          LogControllerMixin, EventControllerMixin,
          AftermathControllerMixin, RefreshControllerMixin):
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
