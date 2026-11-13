#!/usr/bin/env python3
"""repo-kit v2.0 仓库不变量校验器。

设计目标：
- 内容契约用 JSON Schema 表达，不用手写正则解析 YAML；
- Markdown 链接/标题用解析器提取，不把代码块里的示例当真实链接；
- 方向性关系只存一侧，对称关系显式声明；
- 支持 baseline / changed-only，让存量仓库可以渐进接入；
- 能查结构，不假装能判断语义真假。

用法：
    python scripts/check_repo.py --root .
    python scripts/check_repo.py --root . --json
    python scripts/check_repo.py --root . --baseline .repo-baseline.json
    python scripts/check_repo.py --root . --changed-since origin/main
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    yaml = None

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - dependency guard
    MarkdownIt = None

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - optional dependency
    Draft202012Validator = None

LINK_SCHEMES = ("http://", "https://", "mailto:", "tel:", "javascript:", "data:")
ADR_RE = re.compile(r"ADR-(\d{4})-[a-z0-9-]+\.md$")
ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
PITFALL_FIELDS = ("summary", "symptom", "cause", "detect", "avoid", "recover", "applies_to", "severity")
SPEC_VERSION = "2.0.0"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, code: str, severity: str, path: str, message: str, line: int | None = None) -> None:
        item = {"code": code, "severity": severity, "file": path, "message": message}
        if line:
            item["line"] = line
        raw = f"{code}|{path}|{message}".encode("utf-8")
        item["fingerprint"] = hashlib.sha256(raw).hexdigest()[:16]
        self.items.append(item)

    @property
    def errors(self) -> list[dict]:
        return [i for i in self.items if i["severity"] == "ERROR" and not i.get("baselined")]

    @property
    def warnings(self) -> list[dict]:
        return [i for i in self.items if i["severity"] == "WARN" and not i.get("baselined")]

    @property
    def baselined(self) -> list[dict]:
        return [i for i in self.items if i.get("baselined")]


def baseline_key(item: dict) -> tuple[str, str, str]:
    return (item["code"], item["file"], item["message"])


def load_baseline(path: Path) -> tuple[str, set]:
    if not path.exists():
        return ("v1", set())
    data = json.loads(path.read_text(encoding="utf-8"))
    version = int(data.get("version", 1))
    if version >= 2:
        return ("v2", {i["fingerprint"] for i in data.get("items", []) if i.get("fingerprint")})
    return ("v1", {(i["code"], i["file"], i["message"]) for i in data.get("items", [])})


def apply_baseline(report: Report, known: tuple[str, set]) -> None:
    mode, keys = known
    if not keys:
        return
    for item in report.items:
        matched = item.get("fingerprint") in keys if mode == "v2" else baseline_key(item) in keys
        if matched:
            item["baselined"] = True


def _escape_github(value: str, prop: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def emit_github(report: Report) -> None:
    for item in report.items:
        if item.get("baselined"):
            continue
        level = "error" if item["severity"] == "ERROR" else "warning"
        file = item["file"].replace("\\", "/")
        line = item.get("line", 1)
        title = _escape_github(item["code"], prop=True)
        message = _escape_github(item["message"])
        print(f"::{level} file={file},line={line},title={title}::{message}")


def build_sarif(report: Report) -> dict:
    items = [i for i in report.items if not i.get("baselined")]
    rules: dict[str, dict] = {}
    results = []
    for item in items:
        code = item["code"]
        rules.setdefault(code, {"id": code, "shortDescription": {"text": code}})
        results.append({
            "ruleId": code,
            "level": "error" if item["severity"] == "ERROR" else "warning",
            "message": {"text": item["message"]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": item["file"].replace("\\", "/")},
                    "region": {"startLine": item.get("line") or 1},
                }
            }],
            "partialFingerprints": {"repoKit/v1": item["fingerprint"]},
        })
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "repo-kit",
                    "version": "2.2.0",
                    "informationUri": "https://example.org/repo-kit",
                    "rules": list(rules.values()),
                }
            },
            "results": results,
        }],
    }


# ---------------------------------------------------------------------------
# JSON Schema subset validator (fallback when jsonschema is unavailable)
# ---------------------------------------------------------------------------
def _type_ok(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_schema(value, schema: dict, path: str = "$", out: list[str] | None = None) -> list[str]:
    """Validate the JSON Schema subset used by schema/frontmatter.schema.json."""
    out = [] if out is None else out
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, t) for t in types):
            out.append(f"{path}: 期望 {expected}，实际 {type(value).__name__}")
            return out

    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: 期望常量 {schema['const']!r}，实际 {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} 不在枚举 {schema['enum']} 内")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: 长度小于 {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: 长度大于 {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: {value!r} 不匹配 {schema['pattern']}")
        if schema.get("format") == "date":
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                out.append(f"{path}: {value!r} 不是 ISO 日期")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: 大于最大值 {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: 元素少于 {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: 元素多于 {schema['maxItems']}")
        if schema.get("uniqueItems"):
            seen = []
            for v in value:
                if v in seen:
                    out.append(f"{path}: 存在重复元素 {v!r}")
                seen.append(v)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{i}]", out)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value or value[key] in (None, ""):
                out.append(f"{path}: 缺少必填字段 {key}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    out.append(f"{path}: 未知字段 {key}")
        for key, sub in props.items():
            if key in value:
                validate_schema(value[key], sub, f"{path}.{key}", out)

    for sub in schema.get("allOf", []):
        validate_schema(value, sub, path, out)

    if "if" in schema:
        probe: list[str] = []
        validate_schema(value, schema["if"], path, probe)
        if not probe and "then" in schema:
            validate_schema(value, schema["then"], path, out)
        if probe and "else" in schema:
            validate_schema(value, schema["else"], path, out)
    return out


def schema_error_messages(value, schema_obj: dict) -> list[str]:
    if Draft202012Validator is not None:
        out = []
        for err in sorted(Draft202012Validator(schema_obj).iter_errors(value), key=lambda e: list(e.path)):
            where = ".".join(str(x) for x in err.path) or "$"
            out.append(f"{where}: {err.message}")
        return out
    return validate_schema(value, schema_obj)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------
def slugify(text: str, mode: str = "legacy") -> str:
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    kept = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
    s = "".join(kept)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if mode == "github":
        # 保留一个显式的 GitHub 兼容入口；当前算法与 legacy 接近，
        # 后续差异（全角标点、emoji、变体选择符）只在这里演进，避免破坏旧仓库。
        s = re.sub(r"-+", "-", s).strip("-")
    return s


def plain_text(inline) -> str:
    parts = []
    for child in inline.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type == "softbreak":
            parts.append(" ")
    return "".join(parts).strip()


def parse_markdown(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[str]]:
    """Return headings (text,line), links (href,line), inline-code tokens."""
    if MarkdownIt is None:
        return [], [], []
    md = MarkdownIt("commonmark")
    try:
        tokens = md.parse(text)
    except Exception:  # noqa: BLE001 - 坏 Markdown 不应让校验器崩溃
        return [], [], []
    headings: list[tuple[str, int]] = []
    links: list[tuple[str, int]] = []
    code_tokens: list[str] = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt is not None and nxt.type == "inline":
                headings.append((plain_text(nxt), (tok.map[0] + 1) if tok.map else 0))
        elif tok.type == "inline":
            line = (tok.map[0] + 1) if tok.map else 0
            for child in tok.children or []:
                if child.type == "link_open":
                    href = child.attrGet("href") or ""
                    links.append((href, line))
                elif child.type == "image":
                    src = child.attrGet("src") or ""
                    links.append((src, line))
                elif child.type == "code_inline":
                    code_tokens.append(child.content.strip())
                elif child.type == "html_inline":
                    for m in re.finditer(r'href=["\']([^"\']+)["\']', child.content):
                        links.append((m.group(1), line))
    return headings, links, code_tokens


def anchor_set(headings: list[tuple[str, int]], mode: str = "legacy") -> set[str]:
    counts: dict[str, int] = {}
    anchors: set[str] = set()
    for text, _ in headings:
        base = slugify(text, mode)
        if not base:
            continue
        n = counts.get(base, 0)
        slug = base if n == 0 else f"{base}-{n}"
        counts[base] = n + 1
        anchors.add(slug)
    return anchors


def split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text, "缺少 --- frontmatter"
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, text, "frontmatter 没有闭合的 ---"
    raw = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:])
    if yaml is None:
        return None, body, "缺少 PyYAML"
    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except Exception as exc:  # noqa: BLE001
        return None, body, f"YAML 解析失败：{exc}"
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, body, "frontmatter 必须是 mapping"
    return normalize_dates(data), body, None


def normalize_dates(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    if isinstance(value, dict):
        return {k: normalize_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_dates(v) for v in value]
    return value


def nested_structures(value, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                found.append(key)
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, (dict, list)):
                        found.append(f"{key}[{i}]")
            else:
                found.extend(nested_structures(v, key))
    return found


def is_ignored(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def is_generated(rel: str, generated: set[str]) -> bool:
    return rel in generated or rel.startswith("docs/_generated/")


def matches_basename(rel: str, patterns: list[str]) -> bool:
    name = Path(rel).name
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
@dataclass
class Doc:
    path: Path
    rel: str
    text: str
    headings: list[tuple[str, int]]
    anchors: set[str]
    links: list[tuple[str, int]]
    code_tokens: list[str]
    fm: dict | None = None
    page: bool = False


def check_spec(root: Path, report: Report) -> None:
    """校验仓库声明的规范版本（schema/kit.lock.json 的 spec_version）。

    - 无 kit.lock：视为未接入机器层（v1），不报错
    - 有 kit.lock 但缺字段：WARN，提示重跑安装器
    - 格式非法 / 高于校验器支持：ERROR
    - 主版本落后：ERROR（需要迁移）；次版本落后：WARN
    """
    lock_path = root / "schema" / "kit.lock.json"
    if not lock_path.exists():
        return
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        report.add("SPEC_LOCK", "ERROR", "schema/kit.lock.json", f"无法解析：{exc}")
        return
    raw = str(lock.get("spec_version") or "").strip()
    if not raw:
        report.add(
            "SPEC_MISSING", "WARN", "schema/kit.lock.json",
            "缺少 spec_version；重跑 tools/update-repo-kit.ps1 可补齐",
        )
        return
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", raw)
    if not match:
        report.add("SPEC_FORMAT", "ERROR", "schema/kit.lock.json", f"spec_version 不是 SemVer：{raw!r}")
        return
    repo_ver = tuple(int(x) for x in match.groups())
    cur_ver = tuple(int(x) for x in SPEC_VERSION.split("."))
    if repo_ver > cur_ver:
        report.add(
            "SPEC_NEWER", "ERROR", "schema/kit.lock.json",
            f"仓库规范 {raw} 高于校验器支持的 {SPEC_VERSION}，请升级 repo-kit",
        )
    elif repo_ver[0] < cur_ver[0]:
        report.add(
            "SPEC_OUTDATED", "ERROR", "schema/kit.lock.json",
            f"仓库规范 {raw} 落后主版本 {SPEC_VERSION}，需要按迁移文档升级",
        )
    elif repo_ver[1] < cur_ver[1]:
        report.add(
            "SPEC_OUTDATED", "WARN", "schema/kit.lock.json",
            f"仓库规范 {raw} 落后次版本 {SPEC_VERSION}",
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="repo-kit v2.4 不变量校验器")
    ap.add_argument("--root", default=".", help="仓库根目录")
    ap.add_argument("--json", action="store_true", help="JSON 输出（等价于 --format json）")
    ap.add_argument("--format", choices=["text", "json", "sarif", "github"], default="text", help="输出格式")
    ap.add_argument("--baseline", default="", help="baseline JSON 文件")
    ap.add_argument("--write-baseline", default="", help="把当前问题写入 baseline JSON")
    ap.add_argument("--baseline-format", choices=["v1", "v2"], default="v1", help="写 baseline 的格式")
    ap.add_argument("--changed-since", default="", help="只报告该 git ref 之后变更的文件")
    ap.add_argument("--changed-files", default="", help="newline 分隔的变更文件列表（用于 CI）")
    ap.add_argument("--allow-truth-only", action="store_true", help="允许真源单独变更（纯重构逃生口）")
    ap.add_argument("--strict-summary", action="store_true", help="standard/index/decision 缺 summary 时 WARN")
    ap.add_argument("--check-git", action="store_true", help="校验 source_commit 是否存在于 git")
    ap.add_argument("--today", default="", help="覆盖今天的日期（YYYY-MM-DD），用于测试")
    args = ap.parse_args()
    if args.json:
        if args.format not in ("text", "json"):
            print("--json 与 --format sarif/github 不能同时使用", file=sys.stderr)
            return 2
        args.format = "json"

    if yaml is None or MarkdownIt is None:
        print("DEPENDENCY_MISSING: 请先安装 requirements.txt（PyYAML、markdown-it-py）", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    tax_path = root / "schema/taxonomy.json"
    schema_path = root / "schema/frontmatter.schema.json"
    sources_path = root / "schema/sources.json"
    for p in (tax_path, schema_path):
        if not p.exists():
            print(f"CONFIG_MISSING: {p}", file=sys.stderr)
            return 2

    tax = json.loads(tax_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    sources_cfg = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    slugger_mode = tax.get("slugger", "legacy")
    context_files = tax.get("context_files") or []
    exempt_basenames = tax.get("exempt_basenames") or []
    summary_policy = tax.get("summary_policy", "off")
    skills_cfg = tax.get("skills") or {}
    link_only_files = tax.get("link_only_files") or []
    skill_schema = None
    if skills_cfg.get("schema"):
        skill_schema_path = root / str(skills_cfg["schema"])
        if skill_schema_path.exists():
            skill_schema = json.loads(skill_schema_path.read_text(encoding="utf-8"))
        else:
            print(f"CONFIG_MISSING: {skill_schema_path}", file=sys.stderr)
            return 2

    report = Report()
    ignored = tax.get("ignore", [])
    included = tax.get("include") or []
    generated = set(tax.get("generated_files", []))
    entry_files = set(tax.get("entry_files", []))

    docs: dict[str, Doc] = {}
    context_or_exempt: set[str] = set()
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if included and not any(fnmatch.fnmatch(rel, pat) for pat in included):
            continue
        if is_ignored(rel, ignored):
            continue
        if p.name.startswith("_") or p.name.endswith(".template.md"):
            continue
        if matches_basename(rel, context_files) or matches_basename(rel, exempt_basenames):
            context_or_exempt.add(rel)
        if any(fnmatch.fnmatch(rel, pat) for pat in link_only_files):
            context_or_exempt.add(rel)
        text = p.read_text(encoding="utf-8", errors="replace")
        headings, links, code_tokens = parse_markdown(text)
        docs[rel] = Doc(p, rel, text, headings, anchor_set(headings, slugger_mode), links, code_tokens)

    # 技能 profile：SKILL.md 用独立 schema，不套通用页面契约
    skill_files: set[str] = set()
    if skills_cfg.get("glob"):
        for rel in docs:
            if fnmatch.fnmatch(rel, str(skills_cfg["glob"])):
                skill_files.add(rel)
                context_or_exempt.add(rel)

    seen_skill_names: dict[str, str] = {}
    min_description = int(skills_cfg.get("min_description", 1))
    for rel in sorted(skill_files):
        fm, _body, fm_error = split_frontmatter(docs[rel].text)
        if fm_error:
            report.add("SKILL_FM", "ERROR", rel, fm_error)
            continue
        if skill_schema is not None:
            for msg in schema_error_messages(fm, skill_schema):
                report.add("SKILL_SCHEMA", "ERROR", rel, msg)
        name = str(fm.get("name", "") or "")
        if skills_cfg.get("name_matches_dir") and name and Path(rel).parent.name != name:
            report.add("SKILL_NAME_DIR", "ERROR", rel, f"name={name!r} 与目录 {Path(rel).parent.name!r} 不一致")
        if name:
            if name in seen_skill_names:
                report.add("SKILL_NAME_DUP", "ERROR", rel, f"技能 name 与 {seen_skill_names[name]} 重复：{name}")
            else:
                seen_skill_names[name] = rel
        description = str(fm.get("description", "") or "")
        if description and len(description) < min_description:
            report.add("SKILL_DESC_SHORT", "ERROR", rel, f"description 只有 {len(description)} 字符，建议至少 {min_description}")

    pages: dict[str, Doc] = {}
    by_id: dict[str, str] = {}
    defines: dict[str, str] = {}
    relations: list[tuple[str, str, str, str]] = []
    pitfalls: list[tuple[str, dict]] = []

    for rel, doc in docs.items():
        if is_generated(rel, generated) or rel in entry_files or rel in context_or_exempt:
            continue
        fm, _body, fm_error = split_frontmatter(doc.text)
        if fm_error:
            report.add("FM_MISSING", "ERROR", rel, fm_error)
            continue
        doc.fm = fm
        doc.page = True
        pages[rel] = doc

        nested = nested_structures(fm)
        if nested:
            report.add("FM_STRUCTURE", "ERROR", rel, "禁止嵌套对象/列表：" + ", ".join(nested))

        if Draft202012Validator is not None:
            for err in sorted(Draft202012Validator(schema).iter_errors(fm), key=lambda e: list(e.path)):
                where = ".".join(str(x) for x in err.path) or "$"
                report.add("SCHEMA", "ERROR", rel, f"{where}: {err.message}")
        else:
            for msg in validate_schema(fm, schema):
                report.add("SCHEMA", "ERROR", rel, msg)

        if fm.get("kind") == "pitfall":
            pitfalls.append((rel, fm))

        pid = str(fm.get("id", ""))
        if not ID_RE.match(pid):
            report.add("ID_FORMAT", "ERROR", rel, f"id 不符合点分小写规范：{pid!r}")
        if pid in by_id:
            report.add("ID_DUP", "ERROR", rel, f"id 与 {by_id[pid]} 重复：{pid}")
        else:
            by_id[pid] = rel

        for key, allowed in (
            ("type", tax["types"]),
            ("kind", tax["kinds"]),
            ("status", tax["statuses"]),
            ("tier", tax["tiers"]),
            ("confidence", tax["confidences"]),
        ):
            if key in fm and fm[key] not in allowed:
                report.add("VOCAB", "ERROR", rel, f"{key}={fm[key]!r} 不在受控词表内")
        for t in fm.get("topics") or []:
            if t not in tax["topics"]:
                report.add("VOCAB", "ERROR", rel, f"topic={t!r} 不在受控词表内")

        d = fm.get("defines")
        if d:
            if d in defines:
                report.add("DEFINES_DUP", "ERROR", rel, f"defines={d} 已在 {defines[d]} 定义过")
            else:
                defines[d] = rel

        # 真源
        st = fm.get("source_type")
        src = fm.get("source")
        if st in ("code", "schema", "doc") and src:
            target = (root / str(src)).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                report.add("SOURCE_OUTSIDE", "ERROR", rel, f"source 不能指向仓库外：{src}")
            else:
                if not target.exists():
                    report.add("SOURCE_MISSING", "ERROR", rel, f"source 指向不存在的真源：{src}")
                elif not target.is_file():
                    report.add("SOURCE_NOT_FILE", "ERROR", rel, f"source 必须是文件，不能是目录：{src}")
        elif st == "url" and src:
            if not str(src).startswith(("http://", "https://")):
                report.add("SOURCE_INVALID_URL", "ERROR", rel, f"URL source 必须以 http(s):// 开头：{src}")
            if not fm.get("source_retrieved"):
                report.add("SOURCE_RETRIEVED", "ERROR", rel, "URL source 缺少 source_retrieved")

        # 附加来源：sources: ["code:...", "doc:...", "url:..."]
        for extra in fm.get("sources") or []:
            extra = str(extra)
            if ":" not in extra:
                report.add("SOURCE_FORMAT", "ERROR", rel, f"sources 元素应为 type:value：{extra!r}")
                continue
            etype, evalue = extra.split(":", 1)
            if etype not in ("code", "schema", "url", "doc"):
                report.add("SOURCE_FORMAT", "ERROR", rel, f"sources 类型必须是 code/schema/url/doc：{extra!r}")
            elif etype == "url":
                if not evalue.startswith(("http://", "https://")):
                    report.add("SOURCE_INVALID_URL", "ERROR", rel, f"URL source 必须以 http(s):// 开头：{evalue}")
            else:
                target = (root / evalue).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    report.add("SOURCE_OUTSIDE", "ERROR", rel, f"source 不能指向仓库外：{evalue}")
                else:
                    if not target.exists():
                        report.add("SOURCE_MISSING", "ERROR", rel, f"source 指向不存在的真源：{evalue}")
                    elif not target.is_file():
                        report.add("SOURCE_NOT_FILE", "ERROR", rel, f"source 必须是文件，不能是目录：{evalue}")

        sc = fm.get("source_commit")
        if sc:
            if not re.fullmatch(r"[0-9a-f]{7,40}", str(sc)):
                report.add("SOURCE_COMMIT_FORMAT", "ERROR", rel, f"source_commit 必须是 7-40 位小写 hex：{sc!r}")
            elif args.check_git:
                inside = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                    capture_output=True, text=True,
                )
                if inside.returncode != 0:
                    report.add("GIT_UNAVAILABLE", "WARN", rel, "当前目录不是 git 仓库，跳过 source_commit 存在性检查")
                else:
                    exists = subprocess.run(
                        ["git", "-C", str(root), "cat-file", "-e", f"{sc}^{{commit}}"],
                        capture_output=True, text=True,
                    )
                    if exists.returncode != 0:
                        report.add("SOURCE_COMMIT_MISSING", "ERROR", rel, f"source_commit 在 git 中不存在：{sc}")

        # 新鲜度（report / archived 不参与过期判定）
        reviewed = fm.get("last_reviewed")
        if reviewed and fm.get("kind") != "report" and fm.get("status") not in ("archived", "resolved"):
            try:
                reviewed_date = dt.date.fromisoformat(str(reviewed))
                window = fm.get("review_days") or tax["review_days"].get(
                    str(fm.get("tier", "normal")), tax["review_days"]["normal"]
                )
                age = (today - reviewed_date).days
                if age > window:
                    severity = "ERROR" if fm.get("tier") == "critical" else "WARN"
                    report.add("STALE", severity, rel, f"距上次复核 {age} 天，超过 {window} 天窗口")
            except ValueError:
                report.add("STALE", "ERROR", rel, f"last_reviewed 不是 ISO 日期：{reviewed!r}")

        if (
            (summary_policy == "warn" or args.strict_summary)
            and fm.get("kind") in ("standard", "index", "decision", "pitfall")
            and not fm.get("summary")
        ):
            report.add("SUMMARY_MISSING", "WARN", rel, "standard/index/decision 建议写 summary")

        # 关系
        for item in fm.get("related") or []:
            if ":" not in str(item):
                report.add("REL_FORMAT", "ERROR", rel, f"related 元素应为 id:rel：{item!r}")
                continue
            target, reltype = str(item).split(":", 1)
            if reltype not in tax["relations"]:
                report.add("REL_VOCAB", "ERROR", rel, f"关系类型 {reltype!r} 不在闭集内")
            relations.append((pid, target, reltype, rel))

        # superseded 语义
        if fm.get("status") == "superseded" and not fm.get("superseded_by"):
            report.add("SUPERSEDED_MISSING", "ERROR", rel, "status=superseded 时必须写 superseded_by")
        if fm.get("superseded_by") and str(fm["superseded_by"]) not in by_id:
            # 目标可能稍后才注册，留到统一检查
            pass

    # 关系目标与重复
    seen_directed: dict[tuple[str, str, str], str] = {}
    seen_symmetric: dict[tuple[str, str, str], str] = {}
    for owner, target, reltype, source_rel in relations:
        if target not in by_id:
            report.add("REL_TARGET", "ERROR", source_rel, f"related 指向不存在的 id：{target}")
            continue
        if owner == target:
            report.add("REL_SELF", "WARN", source_rel, f"页面引用了自己：{owner}")
        direction = tax["relations"].get(reltype, {}).get("direction", "directed")
        if direction == "directed":
            key = (owner, target, reltype)
            reverse = (target, owner, reltype)
            if reverse in seen_directed:
                report.add(
                    "REL_BOTH_SIDES", "ERROR", source_rel,
                    f"方向性关系 {reltype} 被写在两侧（{seen_directed[reverse]} / {source_rel}），反向链接应派生",
                )
            seen_directed[key] = source_rel
        else:
            key = (min(owner, target), max(owner, target), reltype)
            if key in seen_symmetric:
                report.add("REL_DUP", "WARN", source_rel, f"对称关系 {reltype} 重复声明（{seen_symmetric[key]} / {source_rel}）")
            seen_symmetric[key] = source_rel

    for rel, doc in pages.items():
        fm = doc.fm or {}
        for field in ("superseded_by", "supersedes"):
            target = fm.get(field)
            if target and str(target) not in by_id:
                report.add("REL_TARGET", "ERROR", rel, f"{field} 指向不存在的 id：{target}")

    # pitfall：字段、适用目标、自引用
    for rel, fm in pitfalls:
        pid = str(fm.get("id", ""))
        for field in PITFALL_FIELDS:
            value = fm.get(field)
            if value is None or value == "" or value == []:
                report.add("PITFALL_FIELD", "ERROR", rel, f"kind=pitfall 缺少 {field}")
        severity = fm.get("severity")
        if severity and severity not in ("low", "medium", "high", "critical"):
            report.add("PITFALL_SEVERITY", "ERROR", rel, f"severity 不在 low/medium/high/critical：{severity!r}")
        for target in fm.get("applies_to") or []:
            target = str(target)
            if target == pid:
                report.add("PITFALL_SELF", "ERROR", rel, "applies_to 不能指向自己")
            elif target not in by_id and target not in seen_skill_names:
                report.add("PITFALL_TARGET", "ERROR", rel, f"applies_to 指向不存在的页面/技能：{target}")

    # 链接与锚点
    inbound: set[str] = set()
    # related 也是入链：只通过关系引用、没有正文链接的页面不应被判孤儿
    for _owner, target, _reltype, _source_rel in relations:
        if target in by_id:
            inbound.add(by_id[target])
    # pitfall 的 applies_to 也是入链：坑被目标页“引用”，不应判孤儿
    for _rel, fm in pitfalls:
        for target in fm.get("applies_to") or []:
            if str(target) in by_id:
                inbound.add(by_id[str(target)])
    heading_cache: dict[str, set[str]] = {rel: d.anchors for rel, d in docs.items()}

    def headings_for(path: Path) -> set[str]:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = ""
        if rel in heading_cache:
            return heading_cache[rel]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        hs, _, _ = parse_markdown(text)
        anchors = anchor_set(hs, slugger_mode)
        if rel:
            heading_cache[rel] = anchors
        return anchors

    for rel, doc in docs.items():
        for href, line in doc.links:
            raw = href.strip()
            if not raw:
                continue
            if raw.startswith(LINK_SCHEMES) or raw.startswith("//"):
                continue
            filepart, _, anchor = raw.partition("#")
            filepart = urllib.parse.unquote(filepart).split("?", 1)[0]
            anchor = urllib.parse.unquote(anchor)
            if filepart:
                if filepart.startswith("/"):
                    dest = (root / filepart.lstrip("/")).resolve()
                else:
                    dest = (doc.path.parent / filepart).resolve()
            else:
                dest = doc.path.resolve()
            if not dest.exists():
                report.add("LINK_BROKEN", "ERROR", rel, f"本地链接目标不存在：{raw}", line)
                continue
            if dest.is_dir():
                report.add("LINK_DIR", "WARN", rel, f"本地链接指向目录（GitHub 可渲染，确认不是误链）：{raw}", line)
                continue
            try:
                dest_rel = dest.relative_to(root).as_posix()
            except ValueError:
                dest_rel = ""
            if dest_rel and not is_generated(rel, generated):
                inbound.add(dest_rel)
            if anchor and dest.suffix.lower() == ".md":
                if anchor not in headings_for(dest):
                    report.add(
                        "ANCHOR_BROKEN", "ERROR", rel,
                        f"锚点 #{anchor} 在 {dest.name} 中不存在（改过标题？）", line,
                    )

    # 孤儿页（WARN）：只针对 docs 下的普通页；生成页与决策页不算
    for rel in pages:
        if not rel.startswith("docs/"):
            continue
        if rel == "docs/index.md" or "/decisions/" in rel:
            continue
        if rel not in inbound:
            report.add("ORPHAN", "WARN", rel, "没有任何入链，建议挂到 docs/index.md 或补 related")

    # ADR：唯一编号 + 索引同步，不要求连续
    adr_dir = root / "docs/decisions"
    if adr_dir.exists():
        adr_index = adr_dir / "index.md"
        idx_text = adr_index.read_text(encoding="utf-8") if adr_index.exists() else ""
        seen_adr: dict[str, str] = {}
        for p in sorted(adr_dir.glob("ADR-*.md")):
            m = ADR_RE.match(p.name)
            if not m:
                report.add("ADR_NAME", "ERROR", p.relative_to(root).as_posix(), "ADR 文件名应为 ADR-NNNN-kebab-case.md")
                continue
            num = m.group(1)
            if num in seen_adr:
                report.add("ADR_DUP", "ERROR", p.relative_to(root).as_posix(), f"ADR 编号重复：{num}")
            seen_adr[num] = p.name
            if f"ADR-{num}" not in idx_text:
                report.add("ADR_INDEX", "ERROR", "docs/decisions/index.md", f"索引表缺 ADR-{num}")
        for rel, doc in pages.items():
            fm = doc.fm or {}
            target = fm.get("superseded_by")
            if target and str(target) in by_id:
                target_doc = docs.get(by_id[str(target)])
                if target_doc is not None:
                    target_fm = target_doc.fm or {}
                    if target_fm.get("status") != "superseded":
                        report.add("SUPERSEDED_STATUS", "WARN", rel, f"superseded_by 指向的 {target} 状态不是 superseded")
                    if target_fm.get("supersedes") != fm.get("id"):
                        report.add("SUPERSEDED_BACKLINK", "WARN", rel, f"{target} 没有写 supersedes: {fm.get('id')}")

    # 真源漂移：从代码抽取标识符，文档只能引用已存在的键
    for profile, entries in sources_cfg.items():
        if profile.startswith("_"):
            continue
        for entry in entries:
            truth = root / entry["truth"]
            if not truth.exists():
                report.add("TRUTH_MISSING", "ERROR", entry["truth"], "真源文件不存在")
                continue
            patterns = entry.get("patterns") or [entry.get("pattern", "")]
            truths: set[str] = set()
            for line in truth.read_text(encoding="utf-8", errors="replace").splitlines():
                for pat in patterns:
                    if not pat:
                        continue
                    for m in re.finditer(pat, line):
                        truths.add(m.group(1) if m.groups() else m.group(0))
            token_re = re.compile(entry.get("token_pattern", r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$"))
            allowed = truths | set(entry.get("allowlist", []))
            for doc_rel in entry.get("docs", []):
                doc = docs.get(doc_rel)
                if doc is None:
                    report.add("SOURCE_MISSING", "ERROR", doc_rel, "映射中的文档不存在")
                    continue
                for token in doc.code_tokens:
                    if token_re.match(token) and token not in allowed:
                        report.add(
                            "TRUTH_DRIFT", "ERROR", doc_rel,
                            f"引用了真源中不存在的标识符 {token!r}（代码改了文档没改，或反之）",
                        )

    # 唯一入口：CLAUDE.md / CURSOR.md 等只允许一行指针
    for name in ("CLAUDE.md", "CURSOR.md", ".cursorrules"):
        p = root / name
        if p.exists():
            lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            if len(lines) > 5 or "AGENTS.md" not in "\n".join(lines):
                report.add("ENTRY_DUP", "ERROR", name, "只允许一行指向 AGENTS.md 的指针，禁止第二份完整规则")

    # 生成产物存在性（内容是否最新由 generate.py --check 负责）
    for rel in generated:
        if not (root / rel).exists():
            report.add("GENERATED_MISSING", "ERROR", rel, "生成产物缺失，请运行 python scripts/generate.py --root . --write")

    # 变更文件集合：git ref + 显式列表（CI 可用 changed-files action 注入）
    changed: set[str] = set()
    if args.changed_since:
        for cmd in (
            ["git", "-C", str(root), "diff", "--name-only", f"{args.changed_since}...HEAD"],
            ["git", "-C", str(root), "diff", "--name-only"],
            ["git", "-C", str(root), "diff", "--name-only", "--cached"],
        ):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                changed.update(line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip())
    if args.changed_files:
        p = Path(args.changed_files)
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip().replace("\\", "/")
                if line:
                    changed.add(line.removeprefix("./"))
    if args.changed_since and not changed:
        report.add("CHANGED_EMPTY", "WARN", ".", "没有检测到变更文件；--changed-since 可能不在 git 仓库中")

    # 同 PR co-change：真源变了，映射文档必须至少变一个
    if changed and not args.allow_truth_only:
        for profile, entries in sources_cfg.items():
            if profile.startswith("_"):
                continue
            for entry in entries:
                if not entry.get("co_change"):
                    continue
                truth = Path(entry["truth"]).as_posix()
                if truth not in changed:
                    continue
                co_docs = entry.get("co_change_docs") or entry.get("docs", [])
                if not (set(co_docs) & changed):
                    report.add(
                        "TRUTH_DOCS_NOT_UPDATED", "ERROR", truth,
                        f"真源 {truth} 已变更，但映射文档均未变更（同 PR 规则）",
                    )

    # baseline
    if args.baseline:
        known = load_baseline(Path(args.baseline))
        apply_baseline(report, known)

    # changed-only 过滤
    if changed:
        report.items = [i for i in report.items if i["file"] in changed or i["file"] == "."]

    # 规范版本（始终检查，不受 changed-only 影响）
    check_spec(root, report)

    if args.write_baseline:
        version = 2 if args.baseline_format == "v2" else 1
        items = []
        for i in report.items:
            if i["severity"] not in ("ERROR", "WARN"):
                continue
            item = {"code": i["code"], "file": i["file"], "message": i["message"]}
            if version == 2:
                item["fingerprint"] = i["fingerprint"]
            items.append(item)
        out = {"version": version, "items": items}
        Path(args.write_baseline).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"BASELINE WRITTEN: {args.write_baseline} ({len(out['items'])} items, v{version})")
        return 0

    if args.format == "json":
        print(json.dumps(
            {
                "errors": len(report.errors),
                "warnings": len(report.warnings),
                "baselined": len(report.baselined),
                "pages": len(pages),
                "items": report.items,
            },
            ensure_ascii=False, indent=2,
        ))
    elif args.format == "sarif":
        print(json.dumps(build_sarif(report), ensure_ascii=False, indent=2))
    elif args.format == "github":
        emit_github(report)
    else:
        for i in report.items:
            mark = "BASELINED" if i.get("baselined") else i["severity"]
            loc = f"{i['file']}:{i['line']}" if i.get("line") else i["file"]
            print(f"[{mark:<9}] {i['code']:<20} {loc}: {i['message']}")
        print(
            "\nREPO CHECK: %s  (ERROR=%d, WARN=%d, BASELINED=%d, pages=%d)\n"
            % ("FAIL" if report.errors else "PASS", len(report.errors), len(report.warnings), len(report.baselined), len(pages))
        )
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
