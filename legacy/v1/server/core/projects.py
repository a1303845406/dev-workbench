"""Project service (FR-02): templates, scaffold, CRUD with delete confirmation.

Directory layout per 数据设计 §2 / §7.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import paths
from .audit import audit
from .config import config_center
from .errors import ApiError, name_conflict
from .persist import atomic_write_json, atomic_write_text, check_safe_name, load_json, now_iso, read_text

SCHEMA = 1

_RULES_SKELETON = """---
schemaVersion: 1
layer: project-rules
---
# 工程规则（自动注入每个提示词）

## 技术栈约定
<!-- 语言/框架/版本 -->

## 编码规范
<!-- 命名/目录/分支约定 -->

## 补充约束
<!-- 只能附加或收紧通用硬约束包，不得放宽；出现放宽性关键词时保存会告警 -->
"""


def list_projects() -> list[dict]:
    root = paths.projects_root()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.name):
        if not d.is_dir() or d.name.startswith("."):
            continue
        pj = d / "project.json"
        if not pj.exists():
            continue
        try:
            meta = load_json(pj)
        except ApiError:
            meta = {"name": d.name, "degraded": True}
        meta["pipelines_active"] = _active_pipelines(d)
        meta["last_write"] = _latest_mtime(d)
        out.append(meta)
    return out


def _active_pipelines(pdir: Path) -> list[dict]:
    idx = pdir / "pipeline" / "state" / "index.json"
    if not idx.exists():
        return []
    try:
        data = load_json(idx)
    except ApiError:
        return []
    return [p for p in data.get("pipelines", []) if p.get("status") in ("running", "suspended")]


def _latest_mtime(pdir: Path) -> str:
    latest = 0.0
    for p in pdir.rglob("*"):
        if p.is_file() and not p.name.endswith(".lock"):
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    return now_iso() if latest == 0.0 else __import__("datetime").datetime.fromtimestamp(
        latest).astimezone().isoformat(timespec="seconds")


def get_project(project: str) -> dict:
    pj = paths.project_dir(project) / "project.json"
    if not pj.exists():
        raise ApiError("PROJECT_NOT_FOUND", 404, "项目不存在", {"project": project})
    return load_json(pj)


def list_templates() -> list[dict]:
    out = []
    for name in config_center.template_dirs():
        tpl = config_center.template(name)
        if not tpl:
            continue
        out.append({"id": name, **{k: v for k, v in tpl.items() if k != "schemaVersion"}})
    return out


def create_project(name: str, template: str, *, root_override: str | None = None) -> dict:
    check_safe_name(name, "项目名")
    target = Path(root_override).resolve() if root_override else paths.project_dir(name)
    if target.exists() and any(target.iterdir()):
        raise name_conflict(f"目录已存在：{target}", {"path": str(target)})
    tpl = config_center.template(template)
    if not tpl:
        raise ApiError("TEMPLATE_NOT_FOUND", 404, "模板不存在", {"template": template})
    report = scaffold_from_template(target, tpl)
    atomic_write_json(target / "project.json", {
        "name": name, "template": template, "created_at": now_iso(), "owner": "",
        "provider": (config_center.providers() or {}).get("default", "mock-demo"),
        "scan_roots": [], "schema_migrations": [],
    })
    atomic_write_text(target / "rules.md", _RULES_SKELETON)
    from .tools_registry import default_tools_json
    atomic_write_json(target / "tools.json", default_tools_json())
    audit("project.create", project=name, target=str(target), detail={"template": template})
    return {"name": name, "path": str(target), "scaffold_report": report}


def scaffold_from_template(target: Path, tpl: dict) -> dict:
    tpl_dir = paths.config_root() / "templates" / _template_dirname(tpl)
    dirs = tpl.get("dirs", [])
    files = tpl.get("files", [])
    created_dirs: list[str] = []
    created_files: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    for rel in dirs:
        p = target / rel
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created_dirs.append(rel)
    for f in files:
        p = target / f["path"]
        if p.exists():
            continue
        src = tpl_dir / f.get("content_ref", "")
        content = read_text(src) if src.exists() else ""
        atomic_write_text(p, content)
        created_files.append(f["path"])
    return {"dirs": created_dirs, "files": created_files}


def _template_dirname(tpl: dict) -> str:
    return str(tpl.get("id") or tpl.get("name") or "default-template")


def scaffold_project(project: str) -> dict:
    meta = get_project(project)
    target = paths.project_dir(project)
    tpl = config_center.template(meta.get("template", "default-template"))
    if not tpl:
        tpl = {"dirs": [], "files": []}
    return scaffold_from_template(target, tpl)


def update_project(project: str, patch: dict) -> dict:
    pdir = paths.project_dir(project)
    pj = pdir / "project.json"
    meta = load_json(pj)
    if "scan_roots" in patch:
        if not isinstance(patch["scan_roots"], list):
            raise ApiError("INVALID_SCAN_ROOTS", 400, "scan_roots 必须为数组")
        meta["scan_roots"] = patch["scan_roots"]
    if "provider" in patch:
        meta["provider"] = patch["provider"]
    atomic_write_json(pj, meta)
    audit("project.update", project=project, detail=patch)
    return meta


def delete_project(project: str, confirm_name: str) -> None:
    if confirm_name != project:
        raise ApiError("DELETE_CONFIRM_MISSING", 400, "confirm_name 与项目名不一致")
    pdir = paths.project_dir(project)
    if not pdir.exists():
        raise ApiError("PROJECT_NOT_FOUND", 404, "项目不存在", {"project": project})
    audit("project.delete", project=project)
    shutil.rmtree(pdir)


def save_custom_template(name: str, from_project: str) -> dict:
    """Save an existing project's structure as a reusable template (FR-02 规则 2)."""
    check_safe_name(name, "模板名")
    src = paths.project_dir(from_project)
    if not src.exists():
        raise ApiError("PROJECT_NOT_FOUND", 404, "来源项目不存在", {"project": from_project})
    tpl_dir = config_root_templates() / name
    if tpl_dir.exists():
        raise name_conflict(f"模板已存在：{name}")
    tpl_dir.mkdir(parents=True)
    dirs, files = [], []
    skip_roots = {"project.json", "rules.md", "tools.json"}
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if rel.parts and rel.parts[0] in ("pipeline", "prompts", "testcases", "exports",
                                          "analysis", "backups", ".trash") or "_tmp" in p.name:
            continue
        if p.is_dir():
            dirs.append(rel.as_posix())
        elif p.is_file() and not p.name.endswith(".lock"):
            content = p.read_text(encoding="utf-8", errors="replace")
            ref = rel.as_posix().replace("/", "__")
            (tpl_dir / ref).write_text(content, encoding="utf-8")
            files.append({"path": rel.as_posix(), "content_ref": ref})
    template = {"schemaVersion": 1, "id": name, "name": name,
                "description": f"来自项目 {from_project} 的自定义模板",
                "dirs": [d for d in dirs if d], "files": files, "conflict": "block"}
    atomic_write_json(tpl_dir / "template.json", template)
    audit("template.save", project=from_project, target=name)
    return template


def create_blank_template(name: str, dirs: list[str], description: str = "") -> dict:
    """Create an empty custom template with a user-defined directory skeleton."""
    check_safe_name(name, "模板名")
    if not isinstance(dirs, list) or not dirs or not all(isinstance(d, str) and d.strip() for d in dirs):
        raise ApiError("INVALID_TEMPLATE_DIRS", 400, "dirs 必须为非空字符串数组")
    clean = sorted({d.strip().replace("\\", "/").strip("/") for d in dirs if d.strip()})
    if any(d.startswith("..") or ":" in d for d in clean):
        raise ApiError("INVALID_TEMPLATE_DIRS", 400, "目录不允许相对上跳或盘符")
    tpl_dir = config_root_templates() / name
    if tpl_dir.exists():
        raise name_conflict(f"模板已存在：{name}")
    tpl_dir.mkdir(parents=True)
    template = {"schemaVersion": 1, "id": name, "name": name,
                "description": description or "自定义空白模板", "dirs": clean, "files": []}
    atomic_write_json(tpl_dir / "template.json", template)
    audit("template.create", target=name, detail={"dirs": clean})
    return template


def delete_custom_template(name: str) -> dict:
    if name == "default-template":
        raise ApiError("TEMPLATE_PROTECTED", 400, "内置模板不可删除")
    check_safe_name(name, "模板名")
    tpl_dir = config_root_templates() / name
    if not tpl_dir.is_dir():
        raise ApiError("TEMPLATE_NOT_FOUND", 404, "模板不存在", {"template": name})
    shutil.rmtree(tpl_dir)
    audit("template.delete", target=name)
    return {"deleted": name}


def config_root_templates():
    return paths.config_root() / "templates"
