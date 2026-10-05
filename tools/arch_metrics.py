# -*- coding: utf-8 -*-
"""架构度量：数字说话，别靠目测。

把 arch-review-metrics 那套（原版是 TypeScript）等价改写成 Python，用来回答
"架构清不清晰"。判据取自 Robert C. Martin 的 software package metrics：

| 指标 | 含义 |
|---|---|
| Ca 传入耦合 | 有多少**别的文件**依赖它 → 越高越稳定、越"动不得" |
| Ce 传出耦合 | 它依赖了多少别的文件 → 越高越容易被牵连 |
| I = Ce/(Ca+Ce) | 0=绝对稳定（该在底层），1=最不稳定（该在顶层/组合根） |
| SDP | A 依赖 B 时必须有 I(A) > I(B)，否则是"依赖指向了更不稳定的方向" |
| ADP | 依赖图不能有环；有环则边界形同虚设 |

**A（抽象度）和 D（离主序列的距离）这里刻意不算**：它们按"包里有多少抽象类/接口"
定义，一个全具体类的 Python 工具包算出来 A 恒等于 0，只会得出"所有模块都远离主序列"
这种零信息量的结论。宁可不给数字，也不编一个。

三种依赖分开统计（这是 Python 版的"剥离 import type"）：
- `runtime`：模块级、真正会执行的 import
- `typing`：`if TYPE_CHECKING:` 里的 —— 只在类型检查期存在
- `lazy`：写在函数/方法体里的 —— 只在真的调用到那一步才生效
**报结论时两套数字都给**：只有 runtime 才是"改动真的会牵连到谁"。

跑法：`python tools/arch_metrics.py [仓库根目录]`（默认当前目录）
"""

import ast
import io
import os
import sys
from collections import defaultdict

# 默认分析"当前工作目录"（在仓库根目录跑），也可以在命令行给一个根目录。
# 写成 cwd 而不是 __file__ 的上级，是为了让这份脚本能直接搬到别的仓库用。
ROOT = os.getcwd()
PKG = "brconsole"
SOURCES = [PKG, "main.py"]
# 这些目录不是交付代码：单测/工具/构建产物/探针样本。算耦合时不带它们玩，
# 否则 tools 里对 brconsole 的 import 会把"谁依赖谁"整个带偏。
NOT_DELIVERED = {"tests", "tools", "build", "dist", "sidecar", "_tkbuild",
                 "probes", "__pycache__", ".git", "_brc"}


def iter_sources(root=ROOT, sources=None):
    """交付代码的 .py 清单。`sources=None` 时按 NOT_DELIVERED 过滤整个 root。"""
    if sources is None:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in NOT_DELIVERED]
            for f in sorted(files):
                if f.endswith(".py"):
                    yield os.path.join(dirpath, f)
        return
    for name in sources:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            yield p
        else:
            for dirpath, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in NOT_DELIVERED]
                for f in sorted(files):
                    if f.endswith(".py"):
                        yield os.path.join(dirpath, f)


def read_text(path):
    """读源码文本。别用 `io.open(p).read()` —— 那样句柄不会关，
    跑一圈度量能刷出几十条 ResourceWarning。"""
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def node_of(path, root=ROOT):
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel


def package_of(path, root=ROOT):
    """当前文件**所属的包**（相对导入的基准）。

    ⚠️ 不能用 `node_of()` 去掉 "/__init__" 之后的结果去推：
    `brconsole/__init__.py` 的节点名是 `brconsole`，但**它自己就是那个包**，
    而 `brconsole/gui.py` 的所属包才是"节点名的上一级"。
    两者混用会让 `__init__.py` 里的 `from .core import X` 解析成 `core`（而不是
    `brconsole/core`）→ 解析不到本地节点 → **整条边被静默丢掉**，
    于是工具"看不见"包 `__init__` 的去向（2026-10-05 实测：正因为这个，
    新加的 init-stays-leaf 规则对真实的违规样本一声不吭）。
    """
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        return rel[: -len("/__init__")]
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def resolve_base(imp_from, here_pkg):
    """`ImportFrom` 的基点解析成路径形式（不含被导入的名字）。

    `here_pkg` 必须是**当前文件所属的包**（见 `package_of`），不是节点名。
    """
    if not imp_from.level:
        return (imp_from.module or "").replace(".", "/")
    parts = [x for x in here_pkg.split("/") if x]
    pkg = parts
    up = imp_from.level - 1
    if up:
        pkg = pkg[: len(pkg) - up] if up <= len(pkg) else []
    if imp_from.module:
        mod = imp_from.module.replace(".", "/")
        return "/".join(pkg + [mod]) if pkg else mod
    return "/".join(pkg)


def collect_imports(node, here_pkg, default_bucket):
    """从一段 AST 里产出 [(bucket, [候选目标...], 顶层模块名, 行号)]。

    带行号是为了让 tools/check_boundaries.py 能复用**同一个**解析器 ——
    两个脚本各写一份解析迟早会分叉。

    候选按"越具体越优先"排列：`from .core import JobConfig` 先试
    `brconsole/core/JobConfig`（那是个类，不是模块），再退回 `brconsole/core`。
    """
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for a in sub.names:
                parts = a.name.split(".")
                cands = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
                out.append((default_bucket, cands, parts[0], sub.lineno))
        elif isinstance(sub, ast.ImportFrom):
            base = resolve_base(sub, here_pkg)
            root = base.split("/")[0] if base else ""
            # ⚠️ 一个名字一条边。写成"一条 ImportFrom 只产出一条记录、候选列表里塞满
            #    所有名字"是错的 —— 那样 `from . import a, b, c, d, e` 只会匹配到 `a`，
            #    其余四条边**静默丢失**（第一版就这么写，gui 的 Ce 从 9 缩到了 5）。
            for a in sub.names:
                if a.name == "*":
                    continue
                cands = [(base + "/" + a.name) if base else a.name]
                if base:
                    cands.append(base)      # 退回模块本身（那个名字可能是类/函数）
                out.append((default_bucket, cands, root, sub.lineno))
    return out


def parse_file(path, root=ROOT):
    here = node_of(path, root)
    here_pkg = package_of(path, root)
    tree = ast.parse(read_text(path), filename=path)
    records = []
    for stmt in tree.body:
        test = getattr(stmt, "test", None)
        if isinstance(stmt, ast.If) and test is not None:
            names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
            if "TYPE_CHECKING" in names:
                records += collect_imports(stmt, here_pkg, "typing")
                continue
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            records += collect_imports(stmt, here_pkg, "runtime")
    for sub in ast.walk(tree):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in sub.body:
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    records += collect_imports(inner, here_pkg, "lazy")
    return records


def build(root=ROOT, sources=None):
    files = sorted(iter_sources(root, sources))
    known = {node_of(p, root) for p in files}
    edges = {"runtime": defaultdict(set), "typing": defaultdict(set), "lazy": defaultdict(set)}
    externals = defaultdict(set)

    def local_root(root_name):
        return bool(root_name) and (root_name in known
                                    or any(k.startswith(root_name + "/") for k in known))

    for p in files:
        here = node_of(p, root)
        for bucket, cands, root_name, _ln in parse_file(p, root):
            tgt = next((c for c in cands if c in known), None)
            if tgt is not None:
                if tgt != here:
                    edges[bucket][here].add(tgt)
            elif root_name and not local_root(root_name):
                externals[here].add(root_name)
    return files, known, edges, externals


def coupling(edge_map, universe):
    ca, ce = defaultdict(set), defaultdict(set)
    for a, tgts in edge_map.items():
        for b in tgts:
            ca[b].add(a)
            ce[a].add(b)
    table = {}
    for n in universe:
        i_in, i_out = len(ca[n]), len(ce[n])
        table[n] = (i_in, i_out, (i_out / float(i_in + i_out)) if (i_in + i_out) else 0.0)
    return table, ca, ce


def cycles(nodes, edge_map):
    """迭代版 Tarjan —— 不用递归，免得被深图爆栈。"""
    index, low, on, stack, comps = {}, {}, set(), [], []
    counter = [0]
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(edge_map.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(edge_map.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    comps.append(sorted(comp))
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
    return comps


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else ROOT
    files, known, edges, externals = build(root)
    universe = sorted(known)
    lines = {node_of(p, root): len(read_text(p).splitlines()) for p in files}
    print("=" * 80)
    print("1. 规模")
    print("=" * 80)
    for n in sorted(universe, key=lambda n: -lines[n]):
        print("   %-28s %5d 行" % (n, lines[n]))
    print("   合计 %d 文件 / %d 行" % (len(universe), sum(lines.values())))

    for bucket, label in (("runtime", "运行时依赖（模块级，真的会执行）"),
                          ("typing", "类型依赖（if TYPE_CHECKING）"),
                          ("lazy", "惰性依赖（写在函数体里的 import）")):
        print()
        print("=" * 80)
        print("2. 耦合表 · %s" % label)
        print("=" * 80)
        table, ca, ce = coupling(edges[bucket], universe)
        if not any(table[n][1] for n in universe):
            print("   （没有这类边）")
            continue
        print("   %-28s %4s %4s %7s  %s" % ("文件", "Ca", "Ce", "I", "它依赖谁"))
        for n in sorted(universe, key=lambda n: (table[n][2], -table[n][0])):
            i_in, i_out, inst = table[n]
            if not i_in and not i_out:
                continue
            print("   %-28s %4d %4d %7s  %s"
                  % (n, i_in, i_out, "%.2f" % inst,
                     ", ".join(sorted(ce[n])) if i_out <= 3 else "(%d 个)" % i_out))
        bad = [(a, b, table[a][2], table[b][2])
               for a in sorted(edges[bucket]) for b in sorted(edges[bucket][a])
               if table[a][2] <= table[b][2]]
        print()
        print("   SDP 违规（A 依赖 B 但 I(A) <= I(B)）：%s"
              % ("无" if not bad else "%d 条" % len(bad)))
        for a, b, ia, ib in bad:
            print("     %-26s -> %-26s I %.2f <= %.2f" % (a, b, ia, ib))

    print()
    print("=" * 80)
    print("3. 环（ADP：有环则边界形同虚设）")
    print("=" * 80)
    for bucket, label in (("runtime", "运行时"), ("typing", "类型"), ("lazy", "惰性")):
        found = cycles(universe, edges[bucket])
        print("   %s：%d 个环" % (label, len(found)))
        for c in found:
            print("     " + " <-> ".join(c))

    print()
    print("=" * 80)
    print("4. 扇入榜（改它的代价最大）")
    print("=" * 80)
    table, ca, ce = coupling(edges["runtime"], universe)
    for n in sorted(universe, key=lambda n: -len(ca[n])):
        if ca[n]:
            print("   %-28s 被 %2d 个文件依赖  %s" % (n, len(ca[n]), ", ".join(sorted(ca[n]))))

    print()
    print("=" * 80)
    print("5. 外部依赖（谁碰了什么）")
    print("=" * 80)
    for n in sorted(externals):
        print("   %-28s %s" % (n, ", ".join(sorted(externals[n]))))

    print()
    print("=" * 80)
    print("6. GUI 边界的机械检查（tkinter 只该出现在哪几个文件里）")
    print("=" * 80)
    tk_files = sorted(n for n in externals if "tkinter" in externals[n])
    print("   碰到 tkinter 的文件：%s" % (", ".join(tk_files) or "无"))
    print("   其余 %d 个文件不碰 GUI 库" % (len(universe) - len(tk_files)))


if __name__ == "__main__":
    main()
