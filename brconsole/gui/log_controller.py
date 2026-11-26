# -*- coding: utf-8 -*-
"""日志面板刷新与滚动跟随。

由 `brconsole/gui/app.py` 按职责抽出；方法体保持原样。
"""

class LogControllerMixin(object):
    def clear_log(self):
        self.log.clear()
        self._dropped_shown = 0          # 清空后重新记账（log.clear 已把 dropped 归零）
        self.view.clear_log()

    def _flush_log(self):
        lines = self.log.drain()
        dropped = self.log.dropped
        if not lines and dropped == self._dropped_shown:
            return
        out = []
        # 丢过的日志必须**说出来**：静默丢日志等于静默丢证据（原生输出开满时会丢）
        if dropped > self._dropped_shown:
            out.append("（日志过快，已丢弃最早的 %d 行）"
                       % (dropped - self._dropped_shown))
            self._dropped_shown = dropped
        out.extend(lines)
        self.view.append_log(out)

    def _log(self, text):
        self.log.add(text)
