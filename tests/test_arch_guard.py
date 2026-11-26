# -*- coding: utf-8 -*-
"""架构守卫必须"真的会红"：造一个环，确认 check_arch 能抓到。"""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_arch  # noqa: E402


class TestArchGuard(unittest.TestCase):

    def _write(self, root, rel, text):
        path = pathlib.Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_clean_project_has_no_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, "brconsole/__init__.py", "")
            self._write(td, "brconsole/a.py", "from . import b\n")
            self._write(td, "brconsole/b.py", "VALUE = 1\n")
            self.assertEqual([], check_arch.check(td))

    def test_runtime_cycle_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, "brconsole/__init__.py", "")
            self._write(td, "brconsole/a.py", "from . import b\n")
            self._write(td, "brconsole/b.py", "from . import a\n")
            problems = check_arch.check(td)
            self.assertEqual(1, len(problems))
            self.assertIn("运行时", problems[0][0])
            self.assertEqual({"brconsole/a", "brconsole/b"}, set(problems[0][1]))

    def test_lazy_cycle_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, "brconsole/__init__.py", "")
            self._write(td, "brconsole/a.py", "def f():\n    from . import b\n")
            self._write(td, "brconsole/b.py", "def g():\n    from . import a\n")
            problems = check_arch.check(td)
            self.assertEqual(1, len(problems))
            self.assertIn("惰性", problems[0][0])


if __name__ == "__main__":
    unittest.main()
