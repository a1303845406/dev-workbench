"""FR-03 scanner: whitelist, guarded walk, structure profile, graded suggestions.

04 文档 §4：realpath whitelist prefix, no symlink follow, depth/count limits,
language stats, dependency manifests, dir health vs template, resumable token.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import paths
from .audit import audit
from .config import config_center
from .errors import ApiError, path_not_found, path_out_of_whitelist
from .persist import atomic_write_json, atomic_write_text, now_iso

DEFAULT_DEPTH = 12
DEFAULT_COUNT = 20000

_DEP_FILES = ["package.json", "requirements.txt", "pyproject.toml", "pom.xml", "go.mod",
              "Cargo.toml", "composer.json", "Gemfile"]

_LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".vue": "Vue", ".java": "Java", ".go": "Go", ".rs": "Rust",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#", ".php": "PHP", ".rb": "Ruby",
    ".md": "Markdown", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".html": "HTML",
    ".css": "CSS", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell", ".drawio": "drawio",
}


def validate_target(project: str, target: str) -> Path:
    meta = _project_meta(project)
    allowed = [str(paths.project_dir(project).resolve())] + [str(Path(r).resolve()) for r in meta.get("scan_roots", [])]
    p = Path(target).expanduser()
    if not p.exists() or not p.is_dir():
        raise path_not_found(target)
    real = p.resolve()
    for root in allowed:
        try:
            real.relative_to(Path(root))
            break
        except ValueError:
            continue
    else:
        raise path_out_of_whitelist(str(real), allowed)
    return real


def _project_meta(project: str) -> dict:
    pj = paths.project_dir(project) / "project.json"
    if not pj.exists():
        raise ApiError("PROJECT_NOT_FOUND", 404, "项目不存在", {"project": project})
    return json.loads(pj.read_text(encoding="utf-8"))


def run_scan(project: str, target: str, *, max_depth: int = DEFAULT_DEPTH,
             max_entries: int = DEFAULT_COUNT, resume_token: dict | None = None) -> dict:
    root = validate_target(project, target)
    pdir = paths.project_dir(project)
    files_done = 0
    truncated = False
    ext_stats: dict[str, int] = {}
    dir_sizes: dict[str, int] = {}
    root_md: list[str] = []
    misplaced_tests: list[str] = []
    deps: dict[str, list[str]] = {}
    stack = [(root, 0)]
    entries = 0
    if resume_token:
        # resumable scan: skip until after last_path
        skip_done = False
    else:
        skip_done = True

    while stack:
        cur, depth = stack.pop()
        if resume_token and not skip_done:
            if str(cur) == resume_token.get("last_path"):
                skip_done = True
            continue
        try:
            children = list(cur.iterdir())
        except OSError:
            continue
        for child in children:
            entries += 1
            if entries > max_entries:
                truncated = True
                break
            if child.is_symlink():
                continue
            if child.is_dir():
                if depth < max_depth:
                    stack.append((child, depth + 1))
                else:
                    truncated = True
                continue
            files_done += 1
            rel = child.relative_to(root)
            ext = child.suffix.lower()
            ext_stats[ext] = ext_stats.get(ext, 0) + 1
            top = rel.parts[0] if len(rel.parts) > 1 else ""
            if top:
                dir_sizes[top] = dir_sizes.get(top, 0) + 1
            if len(rel.parts) == 1 and child.suffix.lower() == ".md":
                root_md.append(child.name)
            if child.name.startswith("test") and "tests" not in rel.parts and child.suffix == ".py":
                misplaced_tests.append(str(rel))
            if child.name in _DEP_FILES:
                deps[child.name] = _read_dep_names(child)
        if truncated:
            break

    languages = {}
    for ext, n in sorted(ext_stats.items(), key=lambda kv: -kv[1]):
        lang = _LANG_BY_EXT.get(ext, ext.lstrip(".") or "other")
        languages[lang] = languages.get(lang, 0) + n

    tpl = config_center.template(_project_meta(project).get("template", "default-template"))
    expected_dirs = tpl.get("dirs", [])
    dirs_health = []
    for d in expected_dirs:
        ok = (root / d).exists()
        dirs_health.append({"path": d, "verdict": "ok" if ok else "missing",
                            "reason": "" if ok else "模板目录缺失"})
    extra_top = [d for d in dir_sizes if d not in expected_dirs and not d.startswith(".")]

    suggestions = []
    for name in root_md[:20]:
        suggestions.append({"id": f"A-{len(suggestions)+1}", "level": "auto",
                            "type": "自动归位", "object": name,
                            "suggest": f"根目录文档建议移入 docs/（如 docs/{name}）", "risk": "低"})
    for rel in misplaced_tests[:20]:
        suggestions.append({"id": f"A-{len(suggestions)+1}", "level": "auto", "type": "自动归位",
                            "object": rel, "suggest": "测试文件建议移入 tests/", "risk": "低"})
    for d in extra_top[:10]:
        suggestions.append({"id": f"M-{len(suggestions)+1}", "level": "manual", "type": "需人工决策",
                            "object": d, "suggest": f"顶层目录 {d} 不在模板清单内，请确认用途", "risk": "中"})

    summary = {
        "scanned_at": now_iso(), "root": str(root), "files_done": files_done,
        "truncated": truncated, "languages": languages, "dirs_health": dirs_health,
        "deps": deps, "resumable_token": None if not truncated else
        {"last_path": str(root), "counters": {"files": files_done, "entries": entries}},
    }
    analysis = pdir / "analysis"
    atomic_write_json(analysis / "扫描摘要.json", summary)
    atomic_write_text(analysis / "结构画像.md", _profile_md(summary))
    atomic_write_text(analysis / "规整建议.md", _suggestions_md(suggestions))
    audit("scan.run", project=project, target=str(root),
          detail={"files": files_done, "truncated": truncated})
    return summary


def _read_dep_names(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = []
    if path.name == "package.json":
        try:
            data = json.loads(text)
            names = sorted(set(list(data.get("dependencies", {})) + list(data.get("devDependencies", {}))))
        except json.JSONDecodeError:
            pass
    elif path.name == "requirements.txt":
        names = [ln.strip().split("==")[0] for ln in text.splitlines()
                 if ln.strip() and not ln.startswith("#")]
    elif path.name in ("pyproject.toml", "Cargo.toml", "Gemfile", "go.mod", "composer.json"):
        names = [ln.strip() for ln in text.splitlines()[:40] if ln.strip()][:20]
    elif path.name == "pom.xml":
        import re
        names = re.findall(r"<artifactId>([^<]+)</artifactId>", text)[:20]
    return names[:30]


def _profile_md(s: dict) -> str:
    lines = ["---", "schemaVersion: 1", "kind: structure-profile", "---", "",
             "# 结构画像", "",
             f"- 扫描时间：{s['scanned_at']}",
             f"- 扫描根：`{s['root']}`",
             f"- 文件数：{s['files_done']}" + ("（已截断，可用续扫）" if s["truncated"] else ""),
             "", "## 语言构成", "", "| 语言 | 文件数 |", "|---|---|"]
    for lang, n in s["languages"].items():
        lines.append(f"| {lang} | {n} |")
    lines += ["", "## 目录健康度（对照模板）", "", "| 目录 | 结论 | 说明 |", "|---|---|---|"]
    for d in s["dirs_health"]:
        lines.append(f"| {d['path']} | {'✅ 存在' if d['verdict'] == 'ok' else '❌ 缺失'} | {d['reason']} |")
    lines += ["", "## 依赖摘要", ""]
    for f, names in s["deps"].items():
        lines.append(f"- `{f}`：{', '.join(names[:12])}{' …' if len(names) > 12 else ''}")
    return "\n".join(lines) + "\n"


def _suggestions_md(suggestions: list[dict]) -> str:
    lines = ["---", "schemaVersion: 1", "kind: organize-suggestions", "---", "",
             "# 规整建议", "",
             "> 分级：自动归位（低风险，可交人工/外部 IDE 执行）；需人工决策（仅列事实与风险）", "",
             "| 编号 | 级别 | 对象 | 建议 | 风险 |", "|---|---|---|---|---|"]
    for s in suggestions:
        lines.append(f"| {s['id']} | {s['type']} | {s['object']} | {s['suggest']} | {s['risk']} |")
    return "\n".join(lines) + "\n"
