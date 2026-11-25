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
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import (applock, autostart, diskspace, locate, notify, power,
               queue_store, taskstore, theme, uikit, gui_view)
from . import __version__
from .layout import FROZEN_SUBDIR
from .core import JobConfig, RenderJob
from .guimodel import (AFTERMATH_NOTHING, AFTERMATH_SHUTDOWN, AFTERMATH_SLEEP,
                       DEFAULT_RETRY, DEPTH_KEEP_LABEL, DEVICES, ENGINES, FORMATS,
                       RETRY_CHOICES, SCENE_NEED_READ_LABEL,
                       checks_from_hidden, coerce_depth, depth_choices, depth_value,
                       format_ext_for, format_from_label, format_label,
                       hidden_from_checks, retarget_extension,
                       retry_note,
                       SCENE_PLACEHOLDERS, FormModel, LogModel, NativeLineFilter,
                       ProgressModel,
                       aftermath_plan, event_line, fields_from_detail,
                       fmt_clock, fmt_duration,
                       frames_preview, frames_to_segments, guess_state_path,
                       new_segment, normalise_segment, segment_label,
                       segments_to_frames,
                       scene_to_config, settle_task_action,
                       state_summary)
from .inspect import (read_blend_info, scene_detail, scene_names,
                      summarize as summarize_blend)

POLL_MS = 80
LOG_MAX_LINES = 4000
TITLE = "blender-render-console"
#: 各模块内部 grid 的统一间距（模块级常量：拆出来的 _build_* 方法也要用）
PAD = {"padx": 6, "pady": 4}
#: 标题栏带版本号：用户经常同时装着几个版本，"这是哪个版本"必须能一眼看到。
#: ⚠️ 版本号**只从** `brconsole.__version__` 取，别在这里再写一份字面量。
#: 标题变了就得同步 `applock.focus_window()` 的查找方式（它按前缀兜底，见那里）。
WINDOW_TITLE = "%s %s · 无头渲染控制台" % (TITLE, __version__)

AUTOSTART_FLAG = autostart.AUTOSTART_FLAG

# 「任务结束后」下拉：显示文案 ↔ 实际值（值就是 guimodel.AFTERMATH_*，别再写一份字面量）
AFTERMATH_LABELS = [("什么都不做", AFTERMATH_NOTHING),
                    ("关机（60 秒倒计时，可取消）", AFTERMATH_SHUTDOWN),
                    ("睡眠", AFTERMATH_SLEEP)]
AFTERMATH_BY_LABEL = {label: value for label, value in AFTERMATH_LABELS}


def _stamp():
    # 带日期：过夜渲染跨天后，"01:23:45"分不清是今天还是昨天
    return time.strftime("%m-%d %H:%M:%S")


def icon_path():
    """app.ico 的位置：源码运行在仓库 `assets/` 下，打包后在 `_brc/assets/` 下。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        return os.path.join(base, FROZEN_SUBDIR, "assets", "app.ico")
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


def fit_combobox(cb, values=None, extra=1):
    """按字体实测算下拉框的 `-width`（字符数），让最长的选项能完整显示。

    为什么要算：ttk 的 `-width` 单位是**字符数**，而它的"字符宽"取自字体的平均
    字符宽 —— 中文是双宽，所以 `width=22` 装不下「一直重启，直到全部渲完（默认）」
    这种 15 个汉字的文案，下拉框里显示成尾部被硬切的半句、连省略号都没有
    （Tk 的 combobox 不做 ellipsis）。
    按 `Font.measure()` 折算一次，换 DPI / 换字号都不会再截断。
    """
    try:
        f = tkfont.Font(root=cb, font=cb.cget("font") or "TkDefaultFont")
        unit = max(1, f.measure("0"))
        widest = max(f.measure(str(v)) for v in (values or cb.cget("values")))
        cb.configure(width=int(round(widest / float(unit))) + extra)
    except Exception:
        pass                                    # 字号/字体探测失败不该挡住建界面


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
        #: 原生输出折叠器（采样进度一秒最多留一条，其余只计数）
        self.native_filter = NativeLineFilter()
        #: 已经提示过多少"被丢弃的日志行"——只报增量，不刷屏
        self._dropped_shown = 0
        self.q = queue.Queue()
        self.job = None
        self.worker = None
        self.stopping = False
        self._inspected_path = None      # 已经读过配置的工程，避免重复读
        self._inspecting = False
        self._inspect_token = 0          # 异步读配置的令牌：切走工程后旧结果必须丢弃
        self._inspect_path = None        # 发起读取时的工程路径（竞态校验用）
        self._inspect_item_id = None     # 发起读取时的队列项（回填快照用）
        self._scene_info = None          # 最近一次读到的工程配置（含各场景详情）
        self._scene_info_cache = {}      # 按工程路径缓存，切回同一工程不用再起 Blender
        self._queue_refreshing = False   # 程序化设置选中项时别触发切项回调
        self._frame_preview = []         # 帧段列表展开后的帧号（实时回显的副产物）
        self._seg_rows = []              # 帧段行的控件句柄（_build 里填充）
        self._queue_mode = False          # 「开始渲染」正在按队列顺序跑
        self._current_queue_item = None   # 当前正在渲染的队列项
        self._selected_queue_id = None    # 右侧详情展示的队列项
        self._last_queue_event = None     # 最后一项的结果，队列收尾时判关机/睡眠用
        self._queue_total = 0             # 本轮要跑几项（状态行显示 2/3 用）
        self._queue_index = 0
        self._modules = {}                # 各可折叠模块的控件句柄（_build 里填）
        self._overview_tags = {}          # 概览模块里的值 Label

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

    # ---------------- 界面搭建 ----------------
    # ---------------- 视图层接口（所有控件由 gui_view 生成）----------------
    def _build(self, root):
        if os.environ.get("BRC_OLD_UI") == "1":
            gui_view.build(self, root)
            self.view = None          # 旧视图没有实现新契约，保留兼容分支
            return
        from .gui_view_new import TkView
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

    # ---------------- 帧段（多段不连续的序列帧） ----------------
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

    # ---------------- 表单动作 ----------------
    @staticmethod
    def _plausible_blender(path):
        """路径看着像真 Blender 才信：文件名里得有 blender。

        demo 会把 sys.executable（python.exe）塞进队列项当 blender——那种项被
        选中时会把 v_blender 回填成 python.exe，之后读取/渲染全变成
        "python -b 工程.blend"，.blend 被当 Python 脚本执行（用户实际踩过）。
        """
        return "blender" in os.path.basename(str(path or "")).lower()

    def _autofill_blender(self):
        try:
            cands = locate.find_blender()
        except Exception:
            cands = []
        self._blender_candidates = list(cands)
        self._sync_blender_versions()
        if cands:
            self.v_blender.set(cands[0])
            if len(cands) > 1:
                self._log("检测到多个 Blender，使用 %s（其余：%s）"
                          % (cands[0], ", ".join(cands[1:3])))

    def _sync_blender_versions(self):
        """把 locate 的候选列表灌进版本下拉（显示父目录名，重名退回全路径）。"""
        self._blender_by_label = {}
        labels = []
        for p in self._blender_candidates:
            parent = os.path.basename(os.path.dirname(p))
            label = parent if parent else p
            if label in self._blender_by_label:      # 同名父目录 → 用全路径区分
                label = p
            self._blender_by_label[label] = p
            labels.append(label)
        self.cmb_blender_ver.configure(values=labels)
        if labels:
            self.v_blender_ver.set(labels[0])
        else:
            self.v_blender_ver.set("")

    def on_blender_version(self, _event=None):
        """版本下拉选择 → 写入 Blender 路径（与手填等价，后续渲染用哪份一目了然）。"""
        path = self._blender_by_label.get(self.v_blender_ver.get())
        if path and path != self.v_blender.get():
            self.v_blender.set(path)
            self._log("Blender 改为：%s" % path)

    def _on_blender_var_changed(self, *args):
        """v_blender 变化时校准版本下拉（双向）：路径等于某候选 → 选中它；
        对不上（手填别的路径）→ 清空。下拉与输入框必须说的是同一件事，
        否则界面在说谎（下拉写着"Blender 5.2"，实际跑的是刚粘的另一个路径）。
        """
        target = os.path.normcase(self.v_blender.get())
        match = next((l for l, p in self._blender_by_label.items()
                      if os.path.normcase(p) == target), None)
        if self.v_blender_ver.get() != (match or ""):
            self.v_blender_ver.set(match or "")

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
            self.v_scene.set(SCENE_NEED_READ_LABEL)
            if not self.v_output.get():
                self.v_output.set(FormModel.complete_output("", path))
            # 选工程即入队并自动读配置（HTML 定稿的交互），不再只填一个路径
            self.on_queue_add()

    def maybe_inspect(self):
        """手填/粘贴路径后失焦时也试着读一次（同一个工程不重复读）。"""
        p = self.v_blend.get().strip()
        if p and os.path.exists(p) and p != self._inspected_path:
            self.on_inspect()

    # ---------------- 读取工程里的渲染配置 ----------------
    def on_inspect(self, force=False):
        """读当前工程的渲染配置。选择/添加队列项后会自动调用（同一个工程不重复读）。

        ⚠️ 异步竞态：读一次要起一次 Blender（几十秒），期间用户可能已切到别的工程。
        每次请求带一个 token，回主线程时校验 token + 路径 + 队列项，旧结果一律丢弃，
        否则会把 A 工程的配置写到 B 项上（HANDOFF 风险 2）。
        """
        if self._inspecting or self.prog.running:
            return                        # 渲染中别去抢 Blender
        blend = self.v_blend.get().strip()
        if not blend or not os.path.exists(blend):
            messagebox.showwarning("还没选工程", "先选一个 .blend 工程文件。")
            return
        if not force and blend == self._inspected_path and self._scene_info:
            return                        # 已经读过，切场景直接用内存里的多场景配置
        blender = self.v_blender.get().strip()
        # 名字里连 blender 都没有（如 demo 泄漏的 python.exe）→ 当没有配置过，重新探测
        if (not blender or not os.path.exists(blender)
                or (not self.demo and not self._plausible_blender(blender))):
            self._autofill_blender()
            blender = self.v_blender.get().strip()
        if not blender or not os.path.exists(blender):
            messagebox.showwarning("缺少 Blender 路径",
                                   "读取工程配置需要先指定 blender.exe。")
            return

        self._inspecting = True
        self._inspect_token += 1
        token = self._inspect_token
        self._inspect_path = blend
        self._inspect_item_id = self._selected_queue_id
        self._inspected_path = blend
        # 换工程了：旧工程的场景列表立刻作废，读完再填（否则会拿旧场景名去渲染）
        self._scene_info = None
        self._reset_scene_combo()
        self.btn_inspect.configure(state="disabled")
        self.lbl_inspect.configure(text="正在读取工程配置…", style="Muted.TLabel")
        self.log.add("读取工程配置：%s（一次会把工程里所有场景都读出来）"
                     % os.path.basename(blend))
        threading.Thread(target=self._inspect_worker,
                         args=(blender, blend, token), daemon=True).start()

    def _inspect_worker(self, blender, blend, token):
        try:
            info = read_blend_info(blender, blend, timeout=300)
        except Exception as e:
            info = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.q.put(("__inspect__", {"token": token, "info": info}))

    def _apply_inspect(self, payload):
        token = (payload or {}).get("token")
        info = (payload or {}).get("info")
        stale = (token != self._inspect_token
                 or (self._inspect_path or "") != self.v_blend.get().strip()
                 or (self._inspect_item_id or "") != (self._selected_queue_id or ""))
        if stale:
            self.log.add("工程配置读取结果已过期（期间切了工程），已丢弃")
            if token == self._inspect_token:
                self._inspecting = False
                self.btn_inspect.configure(state="normal")
            return
        self._inspecting = False
        self.btn_inspect.configure(state="normal")
        if not info or not info.get("ok"):
            msg = (info or {}).get("error", "未知错误")
            self.lbl_inspect.configure(text="读取失败（可手填下面的参数）",
                                       style="Muted.TLabel")
            self.log.add("! 读取工程配置失败：%s" % msg.replace("\n", " "))
            self._scene_info = None
            self._reset_scene_combo()
            # 失败原因只在日志里：日志默认折叠着的话用户根本看不见，
            # 会以为"点了没反应/没修复"。失败时把面板展开。
            panel = getattr(self, "log_panel", None)
            if panel is not None:
                panel.expand()
            return

        self._scene_info = info
        self._scene_info_cache[self.v_blend.get().strip()] = info
        self.btn_inspect.configure(text="重新读取工程配置")
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
        # 工程里本来就 hide_render 的集合 → hidden 列表 → 界面上不勾选
        collections = info.get("collections") or []
        project_hidden = [c.get("name") for c in collections
                          if isinstance(c, dict) and c.get("hidden")]
        self._rebuild_collection_ui(collections, project_hidden)
        self._report_scene_camera(info, active)
        # 自动读取完必须刷新该队列项的 cfg 与帧数，否则详情/后续渲染用旧值
        self._snapshot_selected_item()

    def _fill_from_detail(self, detail, name=""):
        """按一个场景的配置重填表单（引擎/采样/分辨率/输出路径、帧范围都是 per-scene 的）。

        帧范围**整段按工程重填**：读工程配置＝"以工程为准"，此时留着上一次的段
        会让界面显示一堆工程里根本没有的帧（上一版只覆盖第 1 段，用户看到的是
        骨架里残留的 2-3）。读完之后用户照样可以再加段。
        """
        fields = fields_from_detail(detail, self.v_blend.get().strip())
        if fields.get("start") or fields.get("end"):
            seg = new_segment(str(fields.get("start", "")),
                              str(fields.get("end", "")),
                              str(fields.get("step", "1") or "1"))
            self._set_segments([seg])
        for key, var in (("engine", self.v_engine),
                         ("samples", self.v_samples), ("device", self.v_device),
                         ("width", self.v_w), ("height", self.v_h),
                         ("pct", self.v_pct),
                         ("output", self.v_output)):
            if key in fields:
                var.set(fields[key])
        # 格式走显示文案（下拉里是"保持工程设置"这种中文，不是裸 identifier）
        if "file_format" in fields:
            self.v_format.set(format_label(fields["file_format"]))
        # 格式定下来之后位数下拉才有候选；工程里读到的位数交给它回填
        self._sync_depth_ui(format_from_label(self.v_format.get()),
                            fields.get("color_depth"))
        return fields

    # ---------------- 集合显示（勾选＝渲染显示，与 hidden 存储相反）----------------
    def _rebuild_collection_ui(self, collections, hidden=()):
        """读工程配置后重建「集合显示」勾选清单。

        `collections` 是 inspect 出的 [{name, objects, hidden}, ...]；`hidden` 是
        **要隐藏**的名字（来自工程 hide_render 或队列项快照）。界面勾选态由
        `checks_from_hidden()` 反转得到 —— 不要在界面里改 `hidden` 的存储语义。
        """
        for w in self.col_box.winfo_children():
            w.destroy()
        self._collection_vars = {}
        self._collection_objects = {}
        if not collections:
            self.lbl_collections.configure(
                text="读取工程配置后可勾选；勾选＝渲染时显示，取消＝渲染时隐藏",
                style="Muted.TLabel")
            self.hidden_collections = []
            return
        self.lbl_collections.configure(
            text="勾选＝渲染时显示；取消勾选＝渲染时隐藏（只内存生效，不改 .blend）",
            style="Muted.TLabel")
        inner = ttk.Frame(self.col_box, style="Card.TFrame")
        inner.pack(anchor="w", fill="x")
        names = [(item.get("name") if isinstance(item, dict) else str(item))
                 for item in collections]
        checked = set(checks_from_hidden(names, hidden))
        for i, item in enumerate(collections):
            name = item.get("name") if isinstance(item, dict) else str(item)
            nobj = item.get("objects") if isinstance(item, dict) else None
            var = tk.BooleanVar(value=(name in checked))
            var.trace_add("write", lambda *_a: self._on_collection_toggle())
            cb = ttk.Checkbutton(inner, text=("%s（%s 个对象）" % (name, nobj)
                                              if nobj is not None else name),
                                 variable=var)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14), pady=1)
            self._collection_vars[name] = var
            self._collection_objects[name] = nobj
        self.hidden_collections = [n for n, v in self._collection_vars.items()
                                   if not v.get()]
        self._sync_overview()

    def _on_collection_toggle(self):
        """界面勾选变了 → 重算 hidden 列表（存储仍是隐藏列表）+ 刷新当前项快照。"""
        names = list(self._collection_vars)
        checked = [n for n, v in self._collection_vars.items() if v.get()]
        self.hidden_collections = hidden_from_checks(names, checked)
        self._snapshot_selected_item()
        if self.hidden_collections:
            self.log.add("渲染时隐藏：%s（已保存，点开始渲染时生效）"
                         % "、".join(self.hidden_collections))

    def _update_scene_combo(self, names, active):
        """场景下拉：只有一个场景时只显示名字，多个时可切换。"""
        if not names:
            self._reset_scene_combo()
            return
        self.cmb_scene.configure(values=names, state="readonly")
        # 场景名长度由工程决定（"Scene.001" 还算短，中文场景名能到十几个字），
        # 写死 width=26 会被截、ttk 的下拉也不做省略号 —— 按字体实测重算
        fit_combobox(self.cmb_scene, names)
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
        if not self.v_output.get().strip():
            messagebox.showinfo("还没填输出模板", "先填输出模板（或选个 .blend 自动补全）。",
                                parent=self.root)
            return
        d = os.path.dirname(os.path.abspath(self.v_output.get()))
        if os.path.isdir(d):
            try:
                os.startfile(d)                 # Windows
            except Exception as e:
                self._log("打不开目录：%r" % (e,))
        else:
            messagebox.showinfo("目录还不存在", "输出目录还没创建：%s" % d)

    def clear_state(self):
        if self.prog.running:
            messagebox.showinfo("渲染进行中",
                                "正在渲染，断点由任务自己维护。\n要清空请先停止任务。",
                                parent=self.root)
            return
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

    # ---- 输出格式 / 图片位数 ----
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

    # ---- 失败重试（一个下拉） ----
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

    # ---------------- 多工程队列 ----------------
    def _selected_item(self):
        """当前右侧展示的队列项（磁盘上的 queue.json 是唯一数据源）。"""
        q = queue_store.load()
        if not q or not q.get("items"):
            return None
        for it in q["items"]:
            if it.get("id") == self._selected_queue_id:
                return it
        return q["items"][0]

    def _refresh_queue(self):
        """重画左栏队列，并把选中项铺到右侧模块（含概览与标题摘要）。"""
        q = queue_store.load()
        items = (q or {}).get("items", [])
        self.lst_queue.delete(0, "end")
        for it in items:
            cfg = it.get("config") or {}
            name = os.path.basename(cfg.get("blend") or "?")
            frames = cfg.get("frames") or []
            # 卡片第二行给「帧数 · 断点进度」——比重复一遍输出文件名有用得多
            # ⚠️ 断点文件是按**输出目录**放的：两项共用同一目录时会读到同一份，
            #    所以只在「总数跟这一项自己的帧数对得上」时才敢显示。
            out = cfg.get("output_template") or ""
            bp = state_summary(guess_state_path(out)) if out else None
            done = ""
            if bp and frames and bp.get("total") == len(frames):
                done = "已完成 %d/%d" % (bp["done"], bp["total"])
            self.lst_queue.insert(
                "end", " %s | %s | %s | %s" % (
                    name, ("%d 帧" % len(frames)) if frames else "?",
                    it.get("status", queue_store.STATUS_PENDING), done))
            status = it.get("status")
            if status == queue_store.STATUS_DONE:
                self.lst_queue.itemconfig("end", foreground=theme.MUTED)
            elif status == queue_store.STATUS_FAILED:
                self.lst_queue.itemconfig("end", foreground=theme.WARN)
        self.lbl_queue_count.configure(text="%d 项" % len(items))
        self.lbl_queue.configure(text=queue_store.summary(q))
        ids = [it.get("id") for it in items]
        if self._selected_queue_id not in ids:
            self._selected_queue_id = ids[0] if ids else None
        if self._selected_queue_id:
            idx = ids.index(self._selected_queue_id)
            self._queue_refreshing = True
            self.lst_queue.selection_clear(0, "end")
            self.lst_queue.selection_set(idx)
            self.lst_queue.see(idx)
            self._queue_refreshing = False
        # 启动/没有任何编辑时：把选中项的配置铺到右侧，别让左边高亮一项、
        # 右边却全是「—」。用户一旦自己填过（v_blend 非空）就不再覆盖。
        if items and not str(self.v_blend.get() or "").strip():
            self._apply_queue_item(self._selected_item())
        self._sync_overview()

    @staticmethod
    def _choice_label(choices, value):
        """(显示文案, 实际值) 表 → 给人看的文案（下拉里存的是实际值）。"""
        return next((label for label, val in choices if val == value),
                    value or "—")

    @staticmethod
    def _short_choice(choices, value):
        """概览里用的短文案：留空一律说「随工程」，别把长标签塞进四列格子。"""
        raw = str(value or "").strip()
        if not raw:
            return "随工程"
        label = next((label for label, val in choices if val == raw), raw)
        return label.split("（")[0]          # 去掉「（用 .blend 里的）」这类括注

    @staticmethod
    def _short_path(path, limit=24):
        """概览里的路径只留最后两段（…\\out\\demo_a_####）：四列格子里放不下全路径。"""
        s = str(path or "")
        parts = s.replace("/", "\\").split("\\")
        tail = "\\".join([p for p in parts[-2:] if p]) if len(parts) > 1 else s
        short = "…\\" + tail if len(parts) > 2 else tail
        if len(short) <= limit + 2:
            return short
        return "…" + short[-(limit + 1):]

    def _sync_overview(self):
        """概览与四个标题摘要：标题栏永远显示当前值，不用展开模块去找。"""
        if not self._overview_tags:
            return
        self._collect_form()
        segs = self._read_segments()
        frames, _errors = segments_to_frames(segs)
        hidden = [n for n, v in self._collection_vars.items() if not v.get()]
        cfg = (self._selected_item() or {}).get("config") or {}
        bp = state_summary(guess_state_path(self.v_output.get()))
        bp_text = ("%d/%d" % (bp["done"], bp["total"])) if bp else "无"
        samples = str(self.v_samples.get() or "").strip()
        engine = self._short_choice(ENGINES, self.v_engine.get())
        fmt_ident = format_from_label(self.v_format.get())
        fmt = fmt_ident.upper() if fmt_ident else "随工程"
        depth = str(self.v_depth.get() or "").strip()
        if fmt != "随工程" and depth and depth != DEPTH_KEEP_LABEL:
            fmt = "%s · %s 位" % (fmt, depth)
        w, h = str(self.v_w.get() or "").strip(), str(self.v_h.get() or "").strip()
        values = {
            "blend": os.path.basename(self.v_blend.get() or "—") or "—",
            "frames": " · ".join(segment_label(s) for s in segs if s.get("start") or s.get("end")) or "—",
            "count": "%d 帧" % len(frames),
            "engine": ("%s · 采样 %s" % (engine, samples)) if samples else engine,
            "device": self._short_choice(DEVICES, self.v_device.get()),
            "res": ("%s×%s @%s%%" % (w, h, self.v_pct.get() or "100")
                    if (w and h) else "随工程"),
            "format": fmt,
            "output": self._short_path(self.v_output.get() or "") or "—",
            "hidden": ("、".join(hidden[:2]) + ("　等 %d 个" % len(hidden)
                                              if len(hidden) > 2 else ""))
                      if hidden else "（无）",
            "retry": "%s · 断点 %s" % (
                "一直重试" if self.v_retry.get() == DEFAULT_RETRY
                else (self.v_retry.get() or "—"), bp_text),
        }
        for key, var in self._overview_tags.items():
            var.set(values.get(key, "—"))
        overview = getattr(self, "overview", None)
        if overview is not None:
            sel = self._selected_item() or {}
            name = os.path.basename((sel.get("config") or {}).get("blend")
                                    or self.v_blend.get() or "")
            overview.set_head(name or "未选工程",
                              sel.get("status") or queue_store.STATUS_PENDING)
        try:
            self.lbl_sum_overview.configure(
                text="%s · %d 帧" % (values["blend"], len(frames)))
            self.lbl_sum_project.configure(
                text=os.path.basename(self.v_blend.get() or "") or "未选工程")
            self.lbl_sum_frames.configure(text="%d 段 · %d 帧" % (len(segs), len(frames)))
            self.lbl_sum_render.configure(
                text="%s · %s · %sx%s" % (
                    self._choice_label(ENGINES, self.v_engine.get()),
                    self._choice_label(DEVICES, self.v_device.get()),
                    self.v_w.get() or "?", self.v_h.get() or "?"))
            self.lbl_sum_program.configure(
                text=os.path.basename(self.v_blender_ver.get()
                                      or self.v_blender.get() or "—"))
        except AttributeError:
            pass                                  # 初始化过程中摘要 Label 还没建全

    def _apply_queue_item(self, item):
        """把某一项的配置快照铺到右侧表单（切队列项 / 渲染下一项都走它）。"""
        if not item:
            return
        fm = FormModel.from_config(item.get("config") or {},
                                   item.get("blender_exe") or "")
        self._apply_form_model(fm)
        path = fm.blend
        cached = self._scene_info_cache.get(path)
        if cached:
            self._scene_info = cached
            self._inspected_path = path
            self._update_scene_combo(scene_names(cached), cached.get("scene") or "")
            self._rebuild_collection_ui(cached.get("collections") or [],
                                        fm.hidden_collections)
            self.btn_inspect.configure(text="重新读取工程配置", state="normal")
        else:
            self._scene_info = None
            self.btn_inspect.configure(text="读取工程配置", state="normal")
        self._refresh_state_label()
        self._sync_overview()

    def _snapshot_selected_item(self):
        """把当前表单写回选中项的快照（切项/勾选集合/读配置完成时调用）。"""
        item = self._selected_item()
        if not item:
            return
        self._collect_form()
        cfg, errors, _warns = self.form.to_config()
        if errors:
            return                       # 半填状态别写坏快照
        blender = self.v_blender.get().strip() or item.get("blender_exe") or ""
        if not self.demo and not self._plausible_blender(blender):
            blender = ""                 # 别把 python.exe 之类的假路径写回存档
        queue_store.update_config(item["id"], cfg, blender)
        self._sync_overview()

    def _on_queue_select(self, _event=None):
        if getattr(self, "_queue_refreshing", False):
            return
        sel = self.lst_queue.curselection()
        if not sel:
            return
        q = queue_store.load()
        if not q:
            return
        item = q["items"][sel[0]]
        if item.get("id") == self._selected_queue_id:
            # 点的就是当前项：配置不用重铺，但"点条目"本身仍是要看它，日志跟着展开
            panel = getattr(self, "log_panel", None)
            if panel is not None:
                panel.expand()
            return
        self._snapshot_selected_item()   # 先把旧项的修改存回去
        self._selected_queue_id = item.get("id")
        self._apply_queue_item(item)
        # 切到某一项＝要看这一项的情况，日志/进度面板跟着展开
        panel = getattr(self, "log_panel", None)
        if panel is not None:
            panel.expand()
        self.on_inspect()                 # 新工程自动读配置与集合状态

    def on_queue_reorder(self, src, dst):
        q = queue_store.load()
        if not q or not (0 <= src < len(q["items"])) or not (0 <= dst < len(q["items"])):
            return
        if src == dst:
            return
        item_id = q["items"][src]["id"]
        delta = 1 if dst > src else -1
        for _ in range(abs(dst - src)):
            if not queue_store.move(item_id, delta):
                break
        self._refresh_queue()

    # 左栏拖动排序（旧 Listbox 版本；新左栏内部已处理）
    def _on_queue_drag_start(self, event):
        self._drag_from = self.lst_queue.nearest(event.y)

    def _on_queue_drag_move(self, _event):
        return                            # 只在松手时真正移动，避免抖来抖去

    def _on_queue_drag_end(self, event):
        src = getattr(self, "_drag_from", None)
        self._drag_from = None
        if src is None:
            return
        dst = self.lst_queue.nearest(event.y)
        if src == dst:
            return
        q = queue_store.load()
        if not q or not (0 <= src < len(q["items"])) or not (0 <= dst < len(q["items"])):
            return
        item_id = q["items"][src]["id"]
        delta = 1 if dst > src else -1
        for _ in range(abs(dst - src)):
            if not queue_store.move(item_id, delta):
                break
        self._selected_queue_id = item_id
        self._refresh_queue()
        self._log("队列顺序已调整")

    def on_queue_add(self):
        """把当前表单加入队列；按工程路径去重，加入后自动读配置与集合状态。"""
        self._collect_form()
        cfg, errors, _warns = self.form.to_config()
        if errors:
            messagebox.showerror("配置有问题", "\n".join("· " + e for e in errors))
            return None
        blender = (self.form.blender or "").strip()
        if not blender or not os.path.exists(blender):
            messagebox.showerror("配置有问题", "先指定有效的 blender.exe")
            return None
        q = queue_store.load()
        want = os.path.normcase(os.path.abspath(cfg.blend))
        for it in (q or {}).get("items", []):
            old = os.path.normcase(os.path.abspath((it.get("config") or {}).get("blend") or ""))
            if old == want:
                self._selected_queue_id = it.get("id")
                queue_store.update_config(it["id"], cfg, blender)
                self._refresh_queue()
                self._apply_queue_item(it)
                self._log("工程已在队列中，已用当前配置更新它")
                self.on_inspect(force=True)
                return it
        _q, item = queue_store.add(cfg, blender)
        self._selected_queue_id = item["id"]
        self._log("已加入队列：%s（%d 帧）"
                  % (os.path.basename(cfg.blend), len(cfg.frames)))
        self._refresh_queue()
        self.on_inspect(force=True)      # 自动读取配置 + 集合状态，并刷新该项快照
        return item

    def on_queue_remove(self):
        sel = self.lst_queue.curselection()
        if not sel:
            return
        q = queue_store.load()
        if not q:
            return
        item = q["items"][sel[0]]
        if queue_store.remove(item.get("id")):
            self._log("已从队列移除：%s" % os.path.basename(item["config"]["blend"]))
        self._selected_queue_id = None
        self._refresh_queue()
        nxt = self._selected_item()
        if nxt:
            self._apply_queue_item(nxt)

    def on_queue_move(self, delta):
        sel = self.lst_queue.curselection()
        if not sel:
            return
        q = queue_store.load()
        if q and queue_store.move(q["items"][sel[0]].get("id"), delta):
            self._selected_queue_id = q["items"][sel[0]].get("id")
            self._refresh_queue()

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
        # 「显示原生输出」只是界面偏好，不进 JobConfig（`FormModel.show_native` 已删除：
        # 它当年存进来之后全项目没有一处读它 —— AGENTS 坑 35 的同款）

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
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        # 模型必须在这里就复位：`job_start` 事件要等后台线程跑起来才到（中间还有
        # 建目录、读/写断点这些磁盘 IO），而 `_refresh` 每 80ms 就用模型刷一次界面 ——
        # 不复位的话，进度条会先跳回上一轮的百分比再归零，状态栏也会闪一下旧文案。
        self.prog.reset()
        self.pb["value"] = 0
        self._refresh()
        # 这次从哪个断点开始，开始前就说清楚（否则"看着像从零开始"）
        self._refresh_state_label()
        self.btn_clear_state.configure(state="disabled")
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        # 把"哪几段、共几帧"写进日志：过夜挂机时回头查日志，得能一眼看出这次渲的是什么
        nseg = segments if segments is not None else len(self.form.segments)
        self._log("开始渲染：%s%s（%d 段，%d 帧 —— %s）"
                  % (cfg.blend, "（场景 %s）" % cfg.scene if cfg.scene else "",
                     nseg, len(cfg.frames),
                     self.lbl_frames.cget("text")))
        # 首帧准备提示前置：大工程读 .blend + 加载贴图 + 上传 GPU 实测可达 110s，
        # 这段时间 Blender 没有任何 stdout，用户最容易误判卡死而点停止（本项目真实事故）。
        self._log("  提示：首帧准备（读工程/加载贴图/上传场景）大工程可能要 1～3 分钟，"
                  "期间无进度输出属正常，请先耐心等待再决定是否停止")

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

    # ---------------- demo（自检/截图用）----------------
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
