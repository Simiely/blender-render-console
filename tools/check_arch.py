# -*- coding: utf-8 -*-
"""架构守卫：依赖图不允许有环（ADP）。

`arch_metrics.py` 负责解释和度量；本脚本只负责 CI 的"红/绿"判定。
SDP 允许有可解释的例外（兼容门面、同层共享导入），所以这里只卡 0 环；
边界方向由 `check_boundaries.py` 单独卡。

跑法：`python tools/check_arch.py [root]`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arch_metrics as am  # noqa: E402


def check(root=None):
    root = root or am.ROOT
    _files, known, edges, _externals = am.build(root)
    universe = sorted(known)
    problems = []
    for bucket, label in (("runtime", "运行时"),
                          ("typing", "类型"),
                          ("lazy", "惰性")):
        for comp in am.cycles(universe, edges[bucket]):
            problems.append((label, comp))
    return problems


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else am.ROOT
    problems = check(root)
    print("架构检查（依赖环）")
    if not problems:
        print("  运行时 / 类型 / 惰性：0 个环")
        print("\n结果：✅ PASS")
        return 0
    for label, comp in problems:
        print("  ❌ %s依赖成环：%s" % (label, " <-> ".join(comp)))
    print("\n结果：❌ %d 个环" % len(problems))
    return 1


if __name__ == "__main__":
    sys.exit(main())
