"""Project / template / rules / scan / tools / exports / docs endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..core import constraints, exporters, paths, projects, scanner, sanitize
from ..core import tools_registry
from ..core.errors import ApiError, project_locked
from ..core.persist import acquire_lock, atomic_write_text, check_lock, read_text, release_lock
from .deps import client_window

router = APIRouter()


@router.get("/projects")
def list_projects():
    return {"projects": projects.list_projects(), "total": len(projects.list_projects())}


@router.post("/projects", status_code=201)
def create_project(body: dict):
    return projects.create_project(body.get("name", ""), body.get("template", "default-template"),
                                   root_override=body.get("root_override"))


@router.get("/projects/{pid}")
def get_project(pid: str):
    return projects.get_project(pid)


@router.patch("/projects/{pid}")
def update_project(pid: str, body: dict):
    return projects.update_project(pid, body)


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: str, body: dict):
    projects.delete_project(pid, body.get("confirm_name", ""))


@router.post("/projects/{pid}/scaffold")
def scaffold(pid: str):
    return projects.scaffold_project(pid)


@router.get("/templates")
def list_templates():
    return {"templates": projects.list_templates()}


@router.post("/templates", status_code=201)
def save_template(body: dict):
    return projects.save_custom_template(body.get("name", ""), body.get("from_project", ""))


# --------------------------------------------------------------------- rules

@router.patch("/projects/{pid}/rules")
def save_rules(pid: str, request: Request, body: dict):
    projects.get_project(pid)
    holder = check_lock(paths.project_dir(pid) / "rules.md", client_window(request))
    if holder:
        pass  # S-04: warn-level only; surfaced via response field
    path = paths.project_dir(pid) / "rules.md"
    atomic_write_text(path, body.get("content", ""))
    hits = constraints.conflict_check(body.get("content", ""))
    resp = {"ok": True, "conflict_warning": {"hits": hits, "code": "RULES_CONFLICT_WARNING"} if hits else None}
    if holder:
        resp["lock_warning"] = holder
    return resp


@router.get("/projects/{pid}/rules")
def get_rules(pid: str):
    projects.get_project(pid)
    p = paths.project_dir(pid) / "rules.md"
    return {"content": read_text(p) if p.exists() else ""}


# ---------------------------------------------------------------------- scan

@router.post("/projects/{pid}/scan")
def scan(pid: str, body: dict):
    projects.get_project(pid)
    summary = scanner.run_scan(pid, body.get("target", ""),
                               max_depth=int(body.get("max_depth", scanner.DEFAULT_DEPTH)),
                               max_entries=int(body.get("max_entries", scanner.DEFAULT_COUNT)),
                               resume_token=body.get("resume_token"))
    return summary


@router.get("/projects/{pid}/analysis")
def analysis(pid: str):
    projects.get_project(pid)
    d = paths.project_dir(pid) / "analysis"
    out = {}
    for name in ("结构画像.md", "规整建议.md", "扫描摘要.json"):
        p = d / name
        if p.exists():
            out[name] = read_text(p)
    return out


# --------------------------------------------------------------------- tools

@router.get("/projects/{pid}/tools")
def get_tools(pid: str):
    projects.get_project(pid)
    return tools_registry.load_tools(pid)


@router.put("/projects/{pid}/tools")
def put_tools(pid: str, body: dict):
    projects.get_project(pid)
    return tools_registry.save_tools(pid, body)


# ------------------------------------------------------------------- exports

@router.post("/projects/{pid}/exports")
def create_export(pid: str, body: dict):
    projects.get_project(pid)
    scope = body.get("scope", {})
    # sanitize gate over assembled text
    sample = _assemble_sample(pid, body.get("format", "markdown"), scope)
    hits = sanitize.scan(sample)
    blocked, confirm = sanitize.classify(hits)
    if blocked and not body.get("sanitize_confirmed"):
        from ..core.errors import sanitize_blocked
        raise sanitize_blocked(blocked)
    if confirm and not body.get("sanitize_confirmed"):
        return {"pending_confirm": confirm, "status": "awaiting-sanitize-confirm"}
    return exporters.generate_export(pid, body.get("format", "markdown"), scope)


@router.get("/projects/{pid}/exports")
def list_exports(pid: str):
    projects.get_project(pid)
    return {"exports": exporters.list_exports(pid)}


@router.get("/exports/{eid}/download")
def download_export(pid: str, eid: str):
    projects.get_project(pid)
    name, data = exporters.download(pid, eid)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _assemble_sample(pid: str, fmt: str, scope: dict) -> str:
    parts = []
    from ..core import assets
    for p in assets.list_prompts(pid):
        f = paths.project_dir(pid) / p["path"]
        if f.exists():
            parts.append(read_text(f))
    rules = paths.project_dir(pid) / "rules.md"
    if rules.exists():
        parts.append(read_text(rules))
    return "\n".join(parts)


# ----------------------------------------------------------------- doc files

@router.get("/projects/{pid}/docs")
def list_docs(pid: str, request: Request):
    """Context file list for compose step (project-relative paths)."""
    projects.get_project(pid)
    base = paths.project_dir(pid)
    out = []
    for exts in ("*.md", "*.drawio"):
        for f in base.rglob(exts):
            if any(part in (".lock",) for part in f.parts):
                continue
            rel = f.relative_to(base).as_posix()
            out.append({"id": rel, "chars": len(read_text(f)) if f.suffix == ".md" else 0})
    return {"files": sorted(out, key=lambda x: x["id"])}


@router.get("/projects/{pid}/docs/content")
def doc_content(pid: str, path: str):
    projects.get_project(pid)
    base = paths.project_dir(pid).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ApiError("PATH_OUT_OF_WHITELIST", 403, "路径越界", {"path": path})
    if not target.exists() or not target.is_file():
        raise ApiError("PATH_NOT_FOUND", 404, "文件不存在", {"path": path})
    return {"path": path, "content": read_text(target)}


@router.patch("/projects/{pid}/docs/raw")
def doc_write_raw(pid: str, body: dict):
    """Manual overwrite of a project doc (e.g. 滚动底稿 human edit). Path-guarded."""
    projects.get_project(pid)
    base = paths.project_dir(pid).resolve()
    target = (base / body.get("path", "")).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ApiError("PATH_OUT_OF_WHITELIST", 403, "路径越界", {"path": body.get("path")})
    if target.suffix != ".md":
        raise ApiError("INVALID_TARGET", 400, "仅允许覆盖 .md 文档")
    from ..core.persist import atomic_write_text
    atomic_write_text(target, body.get("content", ""))
    return {"ok": True}
