# -*- coding: utf-8 -*-
"""日志面板刷新与滚动跟随。

由 `brconsole/gui/app.py` 按职责抽出；方法体保持原样。
"""

from .common import *  # noqa: F401,F403
from .common import _stamp  # noqa: F401


class LogControllerMixin(object):
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

    def _log(self, text):
        self.log.add(text)
