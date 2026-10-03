# -*- coding: utf-8 -*-
"""blender-render-console 入口（PyInstaller 打包入口也是它）。

    python main.py 工程.blend -s 1 -e 240
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brconsole.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
