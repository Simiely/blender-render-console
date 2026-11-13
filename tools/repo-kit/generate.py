#!/usr/bin/env python3
"""repo-kit v2.3 生成器。

生成而不是手写：
- AGENTS.md：AI/人的唯一入口，保持薄
- llms.txt：机器消费入口
- docs/_generated/index.md：按 type/kind 的导航
- docs/_generated/backlinks.md：反向链接（不写回源文件）
- docs/_generated/pitfalls.md：kind=pitfall 的失败模式索引

用法：
    python scripts/generate.py --root . --write
    python scripts/generate.py --root . --check
"""
from __future__ import annotations

import argparse
import json
import posixpath
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_repo as cr  # noqa: E402


def load_pages(root: Path) -> tuple[dict, dict[str, dict]]:
    tax = json.loads((root / "schema/taxonomy.json").read_text(encoding="utf-8"))
    ignored = tax.get("ignore", [])
    included = tax.get("include") or []
    generated = set(tax.get("generated_files", []))
    entry_files = set(tax.get("entry_files", []))
    context_files = tax.get("context_files") or []
    exempt_basenames = tax.get("exempt_basenames") or []
    skills_glob = (tax.get("skills") or {}).get("glob")
    link_only_files = tax.get("link_only_files") or []
    pages: dict[str, dict] = {}
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if included and not any(cr.fnmatch.fnmatch(rel, pat) for pat in included):
            continue
        if cr.is_ignored(rel, ignored) or p.name.startswith("_") or p.name.endswith(".template.md"):
            continue
        if cr.is_generated(rel, generated) or rel in entry_files:
            continue
        if skills_glob and cr.fnmatch.fnmatch(rel, str(skills_glob)):
            continue
        if any(cr.fnmatch.fnmatch(rel, pat) for pat in link_only_files):
            continue
        if cr.matches_basename(rel, context_files) or cr.matches_basename(rel, exempt_basenames):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _body, err = cr.split_frontmatter(text)
        if err or fm is None:
            continue
        pages[rel] = {"rel": rel, "fm": fm, "title": fm.get("title", fm.get("id", rel))}
    return tax, pages


def rel_link(from_rel: str, to_rel: str) -> str:
    return posixpath.relpath(to_rel, posixpath.dirname(from_rel))


def count_by(pages: dict[str, dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for page in pages.values():
        value = str(page["fm"].get(key, ""))
        if value:
            out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def build_agents(pages: dict[str, dict], tax: dict, root: Path, tool_rel: str = "scripts") -> str:
    total = len(pages)
    critical = sum(1 for p in pages.values() if p["fm"].get("tier") == "critical")
    types = count_by(pages, "type")
    type_line = ", ".join(f"{k}={v}" for k, v in types.items()) or "none"
    template_path = root / "schema/agents.template.md"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
        return (
            template.replace("{{PAGES}}", str(total))
            .replace("{{CRITICAL}}", str(critical))
            .replace("{{TYPES}}", type_line)
            .replace("{{VERSION}}", str(tax.get("version", "?")))
            .replace("{{TOOL}}", tool_rel)
        )
    return f"""# AGENTS.md — 仓库唯一入口

> 生成文件，勿手改。改规则请改 `schema/` 或 `{tool_rel}/generate.py`，再运行 `python {tool_rel}/generate.py --root . --write`。

这是给 AI 与人的**唯一入口**。完整用法见 [docs/usage.md](docs/usage.md)，概念定义见 [docs/concepts/ssot.md](docs/concepts/ssot.md)，决策见 [docs/decisions/index.md](docs/decisions/index.md)。`CLAUDE.md`、`CURSOR.md` 等只允许一行指向本文件。

## 非协商（只放最关键的 6 条）
1. 改真源（源码 / schema / 原始材料）时，**同一个 PR** 带上所有引用它的文档。
2. 同一概念、命令、清单只允许有一个权威位置；别处链接，不复制。
3. 禁止写回 backlinks；反向链接由 `{tool_rel}/generate.py` 生成。
4. 禁止手写 `updated`；新鲜度只用 `last_reviewed` + `review_days`。
5. 禁止自造 `type/kind/status/rel/topic` 词表外的值；禁止新增嵌套 frontmatter。
6. 未经用户确认不装软件、不付费、不对外发送、不改生产库。

## 怎么操作
只有一处权威：[使用说明](docs/usage.md)。本文件不复制命令清单、报错表和新页清单。

## 验收
```bash
python {tool_rel}/check_repo.py --root .
python {tool_rel}/generate.py --root . --check
```
验收信号：`REPO CHECK: PASS`（ERROR=0）。检查器验证不了「这句话是不是真的」；改公开标识符 / schema / onboarding 的 PR，需要人把文档与代码路径并排读一遍。

## 当前规模
- 页面：{total}（critical={critical}）
- 按 type：{type_line}
- 词表版本：{tax.get("version", "?")}

## 边界
机器只保结构、引用、新鲜度与真源标识符；语义正确性靠生成 + 人审。不要把本文件写成第二本规范。
"""


def build_llms(pages: dict[str, dict], tax: dict, root: Path) -> str:
    lines = [
        f"# {tax.get('name', 'Documentation')}",
        "",
        "> 文档索引与机器可读入口：内容契约、类型化关系、证据与新鲜度。",
        "> 生成文件，勿手改。",
        "",
        "## Core",
        "",
    ]
    for label, rel in (
        ("README", "README.md"),
        ("文档索引", "docs/index.md"),
        ("使用说明", "docs/usage.md"),
        ("决策索引", "docs/decisions/index.md"),
    ):
        if (root / rel).exists():
            lines.append(f"- [{label}](./{rel})")
    lines += ["", "## By type", ""]
    for typ in ("tutorial", "how-to", "reference", "explanation"):
        group = [
            p for p in pages.values()
            if p["fm"].get("type") == typ and p["fm"].get("kind") not in ("report", "pitfall")
        ]
        if not group:
            continue
        lines.append(f"### {typ}")
        lines.append("")
        for page in sorted(group, key=lambda x: x["fm"].get("id", "")):
            lines.append(f"- [{page['title']}](./{page['rel']}) — {page['fm'].get('summary', '')}".rstrip(" —"))
        lines.append("")
    reports = [p for p in pages.values() if p["fm"].get("kind") == "report"]
    if reports:
        lines += ["## Reports", ""]
        for page in sorted(reports, key=lambda x: x["fm"].get("id", "")):
            lines.append(f"- [{page['title']}](./{page['rel']}) — {page['fm'].get('summary', '')}".rstrip(" —"))
        lines.append("")
    pitfalls = [p for p in pages.values() if p["fm"].get("kind") == "pitfall"]
    if pitfalls:
        lines += ["## Pitfalls", ""]
        for page in sorted(pitfalls, key=lambda x: (str(x["fm"].get("severity", "")), str(x["fm"].get("id", "")))):
            lines.append(f"- [{page['title']}](./{page['rel']}) — {page['fm'].get('summary', '')}".rstrip(" —"))
        lines.append("")
    examples = [pages[rel] for rel in sorted(pages) if rel.startswith("examples/")]
    if examples:
        lines += ["## Examples", ""]
        for page in examples:
            lines.append(f"- [{page['title']}](./{page['rel']})")
        lines.append("")
    return "\n".join(lines)


def build_index(pages: dict[str, dict], tool_rel: str = "scripts") -> str:
    lines = [
        "---",
        "id: docs.generated.index",
        "title: 生成索引",
        "summary: 从 frontmatter 自动生成的导航；勿手改。",
        "type: reference",
        "kind: index",
        "status: active",
        "owner: \"@platform\"",
        "source_type: doc",
        "source: schema/taxonomy.json",
        "last_reviewed: 2026-11-11",
        "related: []",
        "---",
        "",
        "# 生成索引",
        "",
        f"> 生成文件，勿手改。运行 `python {tool_rel}/generate.py --root . --write` 重建。",
        "",
        "## 按 type",
        "",
    ]
    for typ in ("tutorial", "how-to", "reference", "explanation"):
        group = [
            p for p in pages.values()
            if p["fm"].get("type") == typ and p["fm"].get("kind") not in ("report", "pitfall")
        ]
        if not group:
            continue
        lines.append(f"### {typ}")
        lines.append("")
        lines.append("| 页面 | kind | status | owner | tier | source |")
        lines.append("|---|---|---|---|---|---|")
        for page in sorted(group, key=lambda x: x["fm"].get("id", "")):
            fm = page["fm"]
            link = rel_link("docs/_generated/index.md", page["rel"])
            source = str(fm.get("source", ""))
            lines.append(
                f"| [{page['title']}]({link}) | {fm.get('kind', '')} | {fm.get('status', '')} | "
                f"{fm.get('owner', '')} | {fm.get('tier', 'normal')} | `{source}` |"
            )
        lines.append("")
    reports = [p for p in pages.values() if p["fm"].get("kind") == "report"]
    if reports:
        lines += [
            "## Reports",
            "",
            "| 页面 | status | owner | tier | source |",
            "|---|---|---|---|---|",
        ]
        for page in sorted(reports, key=lambda x: x["fm"].get("id", "")):
            fm = page["fm"]
            link = rel_link("docs/_generated/index.md", page["rel"])
            source = str(fm.get("source", ""))
            lines.append(
                f"| [{page['title']}]({link}) | {fm.get('status', '')} | "
                f"{fm.get('owner', '')} | {fm.get('tier', 'normal')} | `{source}` |"
            )
        lines.append("")
    pitfalls = [p for p in pages.values() if p["fm"].get("kind") == "pitfall"]
    if pitfalls:
        lines += [
            "## Pitfalls",
            "",
            "| 坑 | severity | 症状 | applies_to | status |",
            "|---|---|---|---|---|",
        ]
        for page in sorted(pitfalls, key=lambda x: (str(x["fm"].get("severity", "")), str(x["fm"].get("id", "")))):
            fm = page["fm"]
            link = rel_link("docs/_generated/index.md", page["rel"])
            applies = ", ".join(f"`{x}`" for x in (fm.get("applies_to") or []))
            symptom = str(fm.get("symptom", "")).replace("|", "\\|")
            lines.append(
                f"| [{page['title']}]({link}) | {fm.get('severity', '')} | {symptom} | {applies} | {fm.get('status', '')} |"
            )
        lines.append("")
    lines += [
        "## 全部页面",
        "",
        "| id | 标题 | type | kind | status |",
        "|---|---|---|---|---|",
    ]
    for page in sorted(pages.values(), key=lambda x: x["fm"].get("id", "")):
        fm = page["fm"]
        link = rel_link("docs/_generated/index.md", page["rel"])
        lines.append(
            f"| `{fm.get('id', '')}` | [{page['title']}]({link}) | {fm.get('type', '')} | "
            f"{fm.get('kind', '')} | {fm.get('status', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_pitfall_index(pages: dict[str, dict], tool_rel: str = "scripts") -> str:
    severity_order = {v: i for i, v in enumerate(("critical", "high", "medium", "low"))}
    pitfalls = [p for p in pages.values() if p["fm"].get("kind") == "pitfall"]
    active = [p for p in pitfalls if p["fm"].get("status") != "resolved"]
    resolved = [p for p in pitfalls if p["fm"].get("status") == "resolved"]

    def sort_key(page: dict) -> tuple[int, str]:
        return (severity_order.get(str(page["fm"].get("severity", "")), 99), str(page["fm"].get("id", "")))

    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "---",
        "id: docs.generated.pitfalls",
        "title: 踩坑索引",
        "summary: 从 kind=pitfall 页面自动生成的失败模式索引；勿手改。",
        "type: reference",
        "kind: index",
        "status: active",
        "owner: \"@platform\"",
        "source_type: doc",
        "source: schema/taxonomy.json",
        "last_reviewed: 2026-11-11",
        "related: []",
        "---",
        "",
        "# 踩坑索引",
        "",
        f"> 生成文件，勿手改。运行 `python {tool_rel}/generate.py --root . --write` 重建。",
        "> 每个坑的权威内容在它自己的页面；本页只做导航。",
        "",
        f"## 未解决（{len(active)}）",
        "",
    ]
    if active:
        lines += [
            "| 严重度 | 坑 | 症状 | 检测 | 适用 | status |",
            "|---|---|---|---|---|---|",
        ]
        for page in sorted(active, key=sort_key):
            fm = page["fm"]
            link = rel_link("docs/_generated/pitfalls.md", page["rel"])
            applies = ", ".join(f"`{x}`" for x in (fm.get("applies_to") or []))
            lines.append(
                f"| {fm.get('severity', '')} | [{page['title']}]({link}) | {cell(fm.get('symptom', ''))} | "
                f"{cell(fm.get('detect', ''))} | {applies} | {fm.get('status', '')} |"
            )
    else:
        lines.append("（无）")
    lines += ["", f"## 已解决（{len(resolved)}）", ""]
    if resolved:
        lines += ["| 严重度 | 坑 | 症状 | 适用 |", "|---|---|---|---|"]
        for page in sorted(resolved, key=sort_key):
            fm = page["fm"]
            link = rel_link("docs/_generated/pitfalls.md", page["rel"])
            applies = ", ".join(f"`{x}`" for x in (fm.get("applies_to") or []))
            lines.append(
                f"| {fm.get('severity', '')} | [{page['title']}]({link}) | {cell(fm.get('symptom', ''))} | {applies} |"
            )
    else:
        lines.append("（无）")
    targets: dict[str, list[dict]] = {}
    for page in pitfalls:
        for target in page["fm"].get("applies_to") or []:
            targets.setdefault(str(target), []).append(page)
    if targets:
        lines += ["", "## 按适用范围", ""]
        for target in sorted(targets):
            lines.append(f"### `{target}`")
            lines.append("")
            for page in sorted(targets[target], key=sort_key):
                link = rel_link("docs/_generated/pitfalls.md", page["rel"])
                lines.append(f"- [{page['title']}]({link})（{page['fm'].get('severity', '')}）")
            lines.append("")
    if not pitfalls:
        lines += ["", "> 本仓库还没有 `kind: pitfall` 页面。", ""]
    return "\n".join(lines)


def build_backlinks(pages: dict[str, dict], tax: dict) -> str:
    inbound: dict[str, list[tuple[str, str, str]]] = {}
    for page in pages.values():
        fm = page["fm"]
        owner = str(fm.get("id", ""))
        for item in fm.get("related") or []:
            if ":" not in str(item):
                continue
            target, reltype = str(item).split(":", 1)
            inbound.setdefault(target, []).append((owner, reltype, page["rel"]))
        for target in fm.get("applies_to") or []:
            inbound.setdefault(str(target), []).append((owner, "applies-to", page["rel"]))
    lines = [
        "---",
        "id: docs.generated.backlinks",
        "title: 反向链接",
        "summary: 从 related 与 pitfall 的 applies_to 自动派生的反向链接；勿手改。",
        "type: reference",
        "kind: index",
        "status: active",
        "owner: \"@platform\"",
        "source_type: doc",
        "source: schema/taxonomy.json",
        "last_reviewed: 2026-11-11",
        "related: []",
        "---",
        "",
        "# 反向链接",
        "",
        "> 生成文件，勿手改。方向性关系只存一侧，本页是派生结果；对称关系按规范边去重。",
        "> `applies_to` 是 pitfall 的专用字段，可指向页面 id 或技能 name；技能没有页面可链，故只出现在目标列。",
        "",
        "| 目标 id / 技能 | 来源 id | 关系 | 来源页面 |",
        "|---|---|---|---|",
    ]
    id_to_page = {str(p["fm"].get("id", "")): p for p in pages.values()}
    for target in sorted(inbound):
        for owner, reltype, source_rel in sorted(inbound[target]):
            if reltype == "applies-to":
                label = "applies-to（反向：has-pitfall；来自 applies_to 字段）"
            else:
                meta = tax.get("relations", {}).get(reltype, {})
                direction = meta.get("direction", "directed")
                label = reltype if direction == "symmetric" else f"{reltype}（反向：{meta.get('inverse', 'referenced-by')}）"
            link = rel_link("docs/_generated/backlinks.md", source_rel)
            owner_title = id_to_page.get(owner, {}).get("title", owner)
            lines.append(f"| `{target}` | [{owner_title}]({link}) | {label} | `{source_rel}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="repo-kit v2.0 生成器")
    ap.add_argument("--root", default=".")
    ap.add_argument("--write", action="store_true", help="写入生成文件")
    ap.add_argument("--check", action="store_true", help="只检查生成文件是否最新")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.write and args.check:
        print("--write 与 --check 只能选一个", file=sys.stderr)
        return 2
    if cr.yaml is None or cr.MarkdownIt is None:
        print("DEPENDENCY_MISSING: 请先安装 requirements.txt（PyYAML、markdown-it-py）", file=sys.stderr)
        return 2
    root = Path(args.root).resolve()
    try:
        tool_rel = Path(__file__).resolve().parent.relative_to(root).as_posix()
    except ValueError:
        tool_rel = "scripts"
    tax, pages = load_pages(root)
    outputs = {
        "AGENTS.md": build_agents(pages, tax, root, tool_rel),
        "llms.txt": build_llms(pages, tax, root),
        "docs/_generated/index.md": build_index(pages, tool_rel),
        "docs/_generated/backlinks.md": build_backlinks(pages, tax),
        "docs/_generated/pitfalls.md": build_pitfall_index(pages, tool_rel),
    }
    problems = []
    for rel, content in outputs.items():
        path = root / rel
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != content:
            problems.append(rel)
            if args.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    if args.json:
        print(cr.json.dumps({"pages": len(pages), "stale": problems, "written": args.write}, ensure_ascii=False, indent=2))
    elif args.write:
        print(f"GENERATED: {len(outputs)} files, pages={len(pages)}")
    else:
        for rel in problems:
            print(f"GENERATED_STALE: {rel}")
        print(f"GENERATE CHECK: {'PASS' if not problems else 'FAIL'} (pages={len(pages)})")
    if args.write:
        return 0
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
