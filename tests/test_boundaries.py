# -*- coding: utf-8 -*-
"""边界检查脚本的**反向用例**。

只跑正向（"真仓库里 0 违规"）证明不了任何事 —— 一个永远返回 `[]` 的检查脚本
照样"全部通过"。所以这里每条规则都造一份**违规样本**，断言它各命中一次；
再造一份**合规样本**，断言一条都不误报。

误报比漏报更致命：第一版规则把组合根和终端适配器都报了出来（12 处误报），
这种检查不会有人看，最后会被关掉。所以"合规样本不误报"这条是**必须**的。

`services` 目录借 arch_metrics 的遍历规则（只收交付代码），所以样本要放在
brconsole/ 与 main.py 这两个位置下。
"""

import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLS = os.path.join(REPO, "tools")
sys.path.insert(0, REPO)
sys.path.insert(0, TOOLS)

import check_boundaries as cb                                        # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="brc-boundary-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def rules_hit(self, rule):
        return [p for p in cb.check_boundaries(self.root) if p[0] == rule]


class TestViolationsAreDetected(Base):
    """每种违规都必须被抓到 —— 否则规则形同虚设。"""

    def test_gui_boundary(self):
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/core.py", "import tkinter\n")
        hits = self.rules_hit("gui-boundary")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("brconsole/core.py", hits[0][1])

    def test_layer_direction(self):
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/gui.py", "import os\n")
        write(self.root, "brconsole/core.py", "from . import gui\n")
        hits = self.rules_hit("layer-direction")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][1], "brconsole/core.py")

    def test_adapters_for_root(self):
        """界面/下层借用一个适配器里的函数 —— 就是 2026-10-05 修掉的那条真事。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/cli.py", "def parse_frames(spec):\n    return []\n")
        write(self.root, "brconsole/gui.py",
              "import tkinter\nfrom .cli import parse_frames\n")
        hits = self.rules_hit("adapters-for-root")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][1], "brconsole/gui.py")

    def test_adapters_for_root_from_a_lower_layer_too(self):
        """不是只盯界面层：任何非组合根都不许依赖适配器。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/cli.py", "X = 1\n")
        write(self.root, "brconsole/core.py", "from . import cli\n")
        hits = self.rules_hit("adapters-for-root")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][1], "brconsole/core.py")

    def test_init_stays_leaf(self):
        """包的 `__init__.py` import 自己的子模块 —— 会成环（2026-10-05 实测复现过）。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "from .core import JobConfig\n")
        write(self.root, "brconsole/core.py", "class JobConfig(object):\n    pass\n")
        hits = self.rules_hit("init-stays-leaf")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][1], "brconsole/__init__.py")

    def test_cycle(self):
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/a.py", "from . import b\n")
        write(self.root, "brconsole/b.py", "from . import a\n")
        hits = self.rules_hit("no-cycles")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("brconsole/a", hits[0][1])

    def test_driver_isolation(self):
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/driver.py", "import bpy\n")
        write(self.root, "brconsole/core.py", "from . import driver\n")
        hits = self.rules_hit("driver-isolated")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][1], "brconsole/core.py")

    def test_stray_print(self):
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/eta.py", "def f():\n    print('进度')\n")
        hits = self.rules_hit("no-stray-print")
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][2], 2)          # 行号要对得上


class TestNoFalsePositives(Base):
    """合规的东西一条都不该报。"""

    def test_clean_tree_is_silent(self):
        write(self.root, "main.py", "from brconsole import gui\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/gui.py", "import tkinter\nfrom . import core\n")
        write(self.root, "brconsole/theme.py", "import tkinter\n")
        write(self.root, "brconsole/core.py", "import os\n")
        self.assertEqual(cb.check_boundaries(self.root), [])

    def test_composition_root_may_import_ui(self):
        """组合根什么都依赖是**职责**，不是违规。"""
        write(self.root, "main.py", "from brconsole.gui import run_gui\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/gui.py", "import tkinter\n")
        self.assertEqual(self.rules_hit("layer-direction"), [])

    def test_init_without_imports_is_fine(self):
        """只放文档和常量的 `__init__` 是**期望**的形态。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", '"""包说明。"""\n\nVERSION = "1.0"\n')
        write(self.root, "brconsole/core.py", "X = 1\n")
        self.assertEqual(self.rules_hit("init-stays-leaf"), [])

    def test_init_may_import_stdlib(self):
        """只拦"import 自己的子模块"，import 标准库不该误报。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "import os\nimport sys\n")
        self.assertEqual(self.rules_hit("init-stays-leaf"), [])

    def test_normal_module_may_import_siblings(self):
        """规则只作用于包的 `__init__.py`，普通模块之间互相 import 是正常的。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", '"""包说明。"""\n')
        write(self.root, "brconsole/core.py", "X = 1\n")
        write(self.root, "brconsole/gui.py", "import tkinter\nfrom . import core\n")
        self.assertEqual(self.rules_hit("init-stays-leaf"), [])

    def test_composition_root_may_import_adapters(self):
        """组合根拉起命令行入口是它的本职 —— 真仓库里 `main.py` 就是这么干的。"""
        write(self.root, "main.py", "from brconsole.cli import main\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/cli.py", "def main():\n    return 0\n")
        write(self.root, "brconsole/gui.py", "import tkinter\n")
        self.assertEqual(self.rules_hit("adapters-for-root"), [])

    def test_ui_may_import_lower_layers(self):
        """界面依赖 core / guimodel 是正常方向，不能误报。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/core.py", "X = 1\n")
        write(self.root, "brconsole/gui.py", "import tkinter\nfrom . import core\n")
        self.assertEqual(self.rules_hit("adapters-for-root"), [])

    def test_terminal_adapter_may_print(self):
        """终端适配器打印就是它的工作。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/cli.py", "def main():\n    print('done')\n")
        self.assertEqual(self.rules_hit("no-stray-print"), [])

    def test_print_under_main_guard_is_fine(self):
        """`if __name__ == "__main__":` 是手动排错入口，不算违规。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/inspect.py",
              "if __name__ == '__main__':\n    print('debug')\n")
        self.assertEqual(self.rules_hit("no-stray-print"), [])

    def test_ui_may_import_ui(self):
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/gui.py", "import tkinter\nfrom . import theme\n")
        write(self.root, "brconsole/theme.py", "import tkinter\n")
        self.assertEqual(cb.check_boundaries(self.root), [])

    def test_importing_names_not_modules_is_fine(self):
        """`from .core import JobConfig` 里 JobConfig 是类不是模块，不能算成依赖 core 的类。"""
        write(self.root, "main.py", "import os\n")
        write(self.root, "brconsole/__init__.py", "")
        write(self.root, "brconsole/core.py", "class JobConfig(object):\n    pass\n")
        write(self.root, "brconsole/gui.py",
              "import tkinter\nfrom .core import JobConfig\n")
        self.assertEqual(cb.check_boundaries(self.root), [])


class TestRealRepoIsClean(unittest.TestCase):
    def test_repo_has_no_violations(self):
        problems = cb.check_boundaries(REPO)
        self.assertEqual(problems, [], "\n".join("%s %s:%s %s" % p for p in problems))


if __name__ == "__main__":
    unittest.main()
