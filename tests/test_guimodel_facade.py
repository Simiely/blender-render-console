# -*- coding: utf-8 -*-
"""`brconsole.guimodel` 门面守卫：只许转发，不许重新长成上帝模块。"""

import ast
import pathlib
import unittest

import brconsole.guimodel as facade


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestGuimodelFacade(unittest.TestCase):

    def test_all_public_symbols_resolve(self):
        missing = [name for name in facade.__all__ if not hasattr(facade, name)]
        self.assertEqual([], missing)
        self.assertGreaterEqual(len(facade.__all__), 80)

    def test_facade_has_no_business_logic(self):
        path = ROOT / "brconsole" / "guimodel" / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = [node.name for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.assertEqual([], bad)

    def test_facade_does_not_import_tkinter(self):
        path = ROOT / "brconsole" / "guimodel" / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertNotIn("tkinter", names)

    def test_concrete_submodules_expose_split_owners(self):
        from brconsole.guimodel.form import FormModel
        from brconsole.guimodel.frames import frames_preview
        from brconsole.guimodel.logs import NativeLineFilter
        from brconsole.guimodel.progress import ProgressModel
        self.assertTrue(callable(frames_preview))
        self.assertTrue(FormModel)
        self.assertTrue(ProgressModel)
        self.assertTrue(NativeLineFilter)


if __name__ == "__main__":
    unittest.main()
