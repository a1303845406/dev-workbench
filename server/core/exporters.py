"""FR-08 adaptive exporters: spec-kit / taskmaster / openspec / markdown.

Pure-function transforms, sanitize gate upstream, meta.json provenance.
"""
from __future__ import annotations

import json
import time
import zipfile
from io import BytesIO
from pathlib import Path

from . import assets, paths
from . import orchestrator as orch
from .errors import ApiError, export_adapter_failed
from .persist import atomic_write_json, atomic_write_text, now_iso, parse_front_matter, read_text


def _pdir(project: str) -> Path:
    return paths.project_dir(project)


def list_exports(project: str) -> list[dict]:
    root = _pdir(project) / "exports"
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        meta = d / "meta.json"
        if d.is_dir() and meta.exists():
            out.append(json.loads(meta.read_text(encoding="utf-8")))
    return out


def generate_export(project: str, fmt: str, scope: dict) -> dict:
    eid = f"{fmt}-{time.strftime('%Y%m%d-%H%M%S')}"
    out = _pdir(project) / "exports" / eid
    files: dict[str, str] = {}
    try:
        if fmt == "spec-kit":
            files.update(_spec_kit(project, scope))
        elif fmt == "taskmaster":
            files.update(_taskmaster(project, scope))
        elif fmt == "openspec":
            files.update(_openspec(project, scope))
        elif fmt == "markdown":
            files.update(_markdown(project, scope))
        else:
            raise ApiError("EXPORT_FORMAT_UNKNOWN", 400, "未知导出格式",
                           {"known": ["spec-kit", "taskmaster", "openspec", "markdown"]})
    except ApiError as e:
        if e.code == "EXPORT_FORMAT_UNKNOWN":
            raise
        raise export_adapter_failed(fmt, e.message) from e
    for rel, content in files.items():
        atomic_write_text(out / rel, content)
    meta = {"schemaVersion": 1, "export_id": eid, "format": fmt, "scope": scope,
            "created_at": now_iso(), "files": list(files.keys())}
    atomic_write_json(out / "meta.json", meta)
    from .audit import audit
    audit("export.generate", project=project, target=eid, detail={"format": fmt})
    return meta


def download(project: str, export_id: str) -> tuple[str, bytes]:
    d = _pdir(project) / "exports" / export_id
    if not d.exists():
        raise ApiError("EXPORT_NOT_FOUND", 404, "导出不存在", {"export_id": export_id})
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                zf.writestr(str(f.relative_to(d)), f.read_bytes())
    return f"{export_id}.zip", buf.getvalue()


# ------------------------------------------------------------------ adapters

def _finalized_srs(project: str) -> str:
    """Prefer FR-01 S6 confirmed SRS doc; fallback to draft doc folder."""
    for p in (_pdir(project) / "docs" / "需求").rglob("*.md"):
        if p.name in ("需求规格说明书.md", "需求规格说明书-V1.0.md"):
            return read_text(p)
    for p in (_pdir(project) / "docs" / "需求" / "阶段记录").glob("S6-*.md"):
        meta, body = parse_front_matter(read_text(p))
        if meta.get("status") == "confirmed":
            return body
    return ""


def _spec_kit(project: str, scope: dict) -> dict:
    files: dict[str, str] = {}
    if scope.get("requirements", True):
        files["spec.md"] = "# Specification\n\n" + (_finalized_srs(project) or "（需求文档尚未产出）")
        files["plan.md"] = "# Implementation Plan\n\n来源：滚动底稿·实现形态细化区。\n"
    if scope.get("tasks", True):
        lines = ["# Tasks", ""]
        data = orch.get_tasks(project)
        for t in data["tasks"]:
            if orch._is_leaf(data["tasks"], t):
                lines.append(f"- [ ] {t['id']} {t['title']}（{t['module']}，依赖: {','.join(t.get('depends_on', [])) or '—'}）")
        files["tasks.md"] = "\n".join(lines) + "\n"
    return files


def _taskmaster(project: str, scope: dict) -> dict:
    files: dict[str, str] = {}
    if scope.get("requirements", True):
        files["PRD.md"] = "# PRD\n\n" + (_finalized_srs(project) or "（需求文档尚未产出）")
    if scope.get("tasks", True):
        data = orch.get_tasks(project)
        arr = [{"id": t["id"], "title": t["title"],
                "description": t.get("description", ""),
                "details": t.get("acceptance", ""),
                "dependencies": t.get("depends_on", []),
                "status": t.get("status", "pending")}
               for t in orch.leaves(data["tasks"])]
        files["tasks.json"] = json.dumps(arr, ensure_ascii=False, indent=2)
    return files


def _openspec(project: str, scope: dict) -> dict:
    name = (scope.get("change_name") or "存量规整").strip() or "存量规整"
    return {"changes/" + name + "/proposal.md":
            "# Proposal\n\n## 动机\n对存量项目做结构规整。\n\n## 方案\n见 analysis/规整建议.md。\n"
            "\n## 影响面\n仅文档位置迁移，不修改源文件（CON-01）。\n",
            "changes/" + name + "/tasks.md": "# Tasks\n\n- [ ] 按规整建议表逐项确认\n"}


def _markdown(project: str, scope: dict) -> dict:
    files: dict[str, str] = {}
    if scope.get("requirements", True):
        srs = _finalized_srs(project)
        if srs:
            files["需求规格说明书.md"] = srs
    if scope.get("tasks", True):
        try:
            data = orch.get_tasks(project)
            files["tasks.json"] = json.dumps(data, ensure_ascii=False, indent=2)
        except ApiError:
            pass
    if scope.get("prompts", False):
        for p in assets.list_prompts(project, status="final"):
            f = _pdir(project) / p["path"]
            files[f"prompts/{p['module']}/{p['task_id']}/{Path(p['path']).name}"] = read_text(f)
    return files
