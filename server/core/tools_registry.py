"""Project-level collaboration tools registry (tools.json, FR-08 / CON-13)."""
from __future__ import annotations

from . import paths
from .config import config_center
from .persist import atomic_write_json, load_json


def default_tools_json() -> dict:
    data = config_center.tools_default() or {"tools": []}
    return {"schemaVersion": 1, "tools": data.get("tools", [])}


def load_tools(project: str) -> dict:
    path = paths.project_dir(project) / "tools.json"
    if not path.exists():
        return default_tools_json()
    data = load_json(path, default={})
    if not data.get("tools"):
        return default_tools_json()
    return data


def save_tools(project: str, data: dict) -> dict:
    tools = data.get("tools")
    if not isinstance(tools, list):
        from .errors import ApiError
        raise ApiError("INVALID_TOOLS", 400, "tools 必须为数组")
    clean = {"schemaVersion": 1, "tools": tools}
    atomic_write_json(paths.project_dir(project) / "tools.json", clean)
    return clean


def enabled_guides(project: str, module_category: str) -> list[dict]:
    """Enabled tool entries matching a module category → guide templates."""
    out = []
    for t in load_tools(project).get("tools", []):
        if not t.get("enabled"):
            continue
        if t.get("category") == module_category and t.get("guide_template"):
            out.append({"name": t.get("name"), "guide_template": t["guide_template"]})
    return out
