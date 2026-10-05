# -*- coding: utf-8 -*-
"""边界检查：把架构评审发现的越界固化成**可执行规则**。

只靠人记的约定活不过半年 —— 评审抓到的每一条，这里都要有一条对应规则。
规则全部来自 2026-10-05 那次实测（数字见 `python tools/arch_metrics.py`）：

| 规则 | 为什么 |
|---|---|
| `gui-boundary` | 只有界面层可以 import tkinter。实测全仓只有 `gui.py` / `theme.py` 碰它，这是"界面逻辑可单测"的前提 |
| `layer-direction` | **包内**底层的文件不许 import 界面层。界面层 `I=1.00`（最不稳定），底层 `I=0.00`，依赖必须指向更稳定的方向（SDP）。**组合根 `main.py` 例外** —— 它天然"什么都知道"，这正是它的职责 |
| `no-cycles` | 运行时依赖图不许有环（ADP）。实测 0 环 |
| `adapters-for-root` | **只有组合根可以依赖适配器**（`cli.py` 是终端适配器）。2026-10-05 实测抓到的真事：界面层 `from .cli import parse_frames` —— 只为了借一个帧范围解析函数，就把"界面依赖命令行"这条方向错误的边建了起来（`gui.py:25`）。共用工具该待在下层，修法见 `core.parse_frames` |
| `init-stays-leaf` | **包的 `__init__.py` 不许 import 子模块**。2026-10-05 实测踩到：把共用常量放进 `__init__` 后，包根成了"谁都要来取一句"的汇聚点；这时谁按 PEP8 在 `__init__` 顶部补一句 `from .core import X`，就会撞上"还没执行到常量定义"的包，报 `ImportError: cannot import name ... from partially initialized module`。共用常量请放**叶子模块**（如 `brconsole/layout.py`）。只拦"import 自己的子模块"，import 标准库不拦 |
| `driver-isolated` | `driver.py` 跑在 **Blender 进程内**（它 `import bpy`），app 侧 import 它必然当场炸。它靠**源码字符串注入**，不是模块依赖 |
| `no-stray-print` | 库代码不许裸 `print` 抢 stdout：进度要走事件回调/日志。例外：**终端适配器** `cli.py`（打印就是它的职责）与 `if __name__ == "__main__":` 里的手动排错入口 |

⚠️ 写规则时**别把正确的东西也判违规**：第一版 `layer-direction` 把组合根、`no-stray-print`
把终端适配器都报了出来（12 处误报）。一个"永远报一堆无关紧要"的检查不会有人看，最后会被关掉 ——
误报比漏报更致命。

**解析器复用 `arch_metrics.py`**，不另写一份 —— 两个脚本各解析一遍，迟早分叉。

跑法：`python tools/check_boundaries.py`（有违规则退出码 1）
"""

import ast
import io
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arch_metrics as am                                            # noqa: E402

# 允许碰 tkinter 的文件（界面层）
UI_FILES = {"brconsole/gui.py", "brconsole/theme.py"}
UI_MODULES = {"brconsole/gui", "brconsole/theme"}
DRIVER = "brconsole/driver.py"
# 组合根：天然"什么都知道"，包内底层不许依赖界面层这条对它不适用
COMPOSITION_ROOT = "main.py"
# 打印到 stdout 就是它的职责的适配器（终端）
PRINT_OK = {"brconsole/cli.py"}
# 适配器层：只该被组合根依赖。别处要用共用工具，请把工具放到下层模块
ADAPTER_MODULES = {"brconsole/cli"}
# 包的 __init__：只该放文档与版本号，不许 import 自己的子模块（会成环）
INIT_FILES = {"brconsole/__init__.py"}


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _stray_prints(path):
    """库代码里的裸 print 行号。`if __name__ == "__main__":` 块内不算。"""
    tree = ast.parse(am.read_text(path), filename=path)
    guarded = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.If):
            names = {n.id for n in ast.walk(stmt.test) if isinstance(n, ast.Name)}
            consts = [c.value for c in ast.walk(stmt.test) if isinstance(c, ast.Constant)]
            if "__name__" in names and "__main__" in consts:
                guarded.update(id(n) for n in ast.walk(stmt))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "print" and id(n) not in guarded]


def check_boundaries(root=None):
    """返回 [(规则, 相对路径, 行号, 说明)]；空列表 = 全部通过。

    `root` 可指向任意目录 —— 单测就是靠这个造违规样本验证规则**真的会命中**
    （一个永远返回 [] 的检查脚本也照样"通过"，只跑正向证明不了任何事）。
    """
    root = root or am.ROOT
    files, known, edges, externals = am.build(root)
    driver_node = am.node_of(os.path.join(root, DRIVER), root)
    problems = []

    for path in files:
        rel = _rel(path, root)
        node = am.node_of(path, root)
        for bucket, cands, root_name, lineno in am.parse_file(path, root):
            tgt = next((c for c in cands if c in known), None)

            # 1) tkinter 只该出现在界面层
            if root_name == "tkinter" and tgt is None and rel not in UI_FILES:
                problems.append(("gui-boundary", rel, lineno,
                                 "只有界面层（%s）可以 import tkinter"
                                 % "、".join(sorted(UI_FILES))))

            if tgt is None or tgt == node:
                continue

            # 2) 包内底层不许反向依赖界面层（组合根除外）
            if tgt in UI_MODULES and node not in UI_MODULES and rel != COMPOSITION_ROOT:
                problems.append(("layer-direction", rel, lineno,
                                 "底层文件依赖了界面层 %s（依赖必须指向更稳定的方向）" % tgt))

            # 2b) 适配器只能被组合根依赖
            if tgt in ADAPTER_MODULES and rel != COMPOSITION_ROOT:
                problems.append(("adapters-for-root", rel, lineno,
                                 "不该依赖终端适配器 %s —— 共用工具请放到下层模块"
                                 "（参照 core.parse_frames 的做法）" % tgt))

            # 2c) 包 __init__ 不许 import 自己的子模块
            if rel in INIT_FILES and tgt is not None:
                problems.append(("init-stays-leaf", rel, lineno,
                                 "包 __init__ 不许 import 子模块 %s —— 子模块又要从包上取东西时"
                                 "会成环（共用常量请放叶子模块，如 brconsole/layout.py）" % tgt))

            # 3) app 侧不许 import driver
            if tgt == driver_node and node != driver_node:
                problems.append(("driver-isolated", rel, lineno,
                                 "driver.py 跑在 Blender 进程内，只能靠源码字符串注入，不能 import"))

        # 4) 库代码的裸 print（终端适配器除外）
        if rel not in PRINT_OK:
            for lineno in _stray_prints(path):
                problems.append(("no-stray-print", rel, lineno,
                                 "库代码里的 print 会抢 stdout，进度请走事件回调/日志"))

    # 5) 运行时依赖环
    for comp in am.cycles(sorted(known), edges["runtime"]):
        problems.append(("no-cycles", "、".join(comp), 0,
                         "运行时依赖成环：边界形同虚设"))
    return problems


RULES = ("gui-boundary", "layer-direction", "adapters-for-root", "init-stays-leaf",
         "no-cycles", "driver-isolated", "no-stray-print")


def main():
    problems = check_boundaries()
    print("边界检查（%d 条规则）" % len(RULES))
    for r in RULES:
        hits = [p for p in problems if p[0] == r]
        print("  %-16s %s" % (r, "✅ 通过" if not hits else "❌ %d 处" % len(hits)))
        for _rule, where, line, msg in hits:
            print("       %s:%s  %s" % (where, line, msg))
    print()
    print("结果：%s" % ("✅ 全部通过" if not problems else "❌ %d 处违规" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
