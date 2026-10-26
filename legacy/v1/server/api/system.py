"""System endpoints: health/version/settings/backups/events(SSE)/audit + config write."""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core import audit as audit_mod
from ..core import backup, paths
from ..core.config import config_center
from ..core.errors import ApiError
from ..core.events import subscribe
from ..core.persist import atomic_write_json, atomic_write_text
from ..core.settings import settings_view

router = APIRouter()

VERSION = "1.0.0"


def _write_config(rel: str, data: dict) -> None:
    """Validate-then-atomically-write a config under configs/ (rolling .bak kept)."""
    atomic_write_json(paths.config_root() / rel, data, schema_version=None)


def _write_config_text(rel: str, text: str) -> None:
    atomic_write_text(paths.config_root() / rel, text)


def _require(cond: bool, code: str, msg: str) -> None:
    if not cond:
        raise ApiError(code, 400, msg)


@router.get("/health")
def health():
    return {"status": "ok", "frontend": paths.frontend_dist_exists()}


@router.get("/version")
def version():
    from ..core.constraints import package_version
    return {"version": VERSION,
            "constraints_package_version": package_version(),
            "config_dir": str(paths.config_root())}


@router.get("/settings")
def settings():
    view = settings_view()
    return view


@router.put("/settings/providers")
def update_providers(body: dict):
    providers = body.get("providers")
    default = body.get("default", "")
    _require(isinstance(providers, list) and providers, "INVALID_PROVIDERS", "providers 必须为非空数组")
    names: list[str] = []
    for p in providers:
        _require(isinstance(p, dict), "INVALID_PROVIDERS", "providers 元素必须为对象")
        for f in ("name", "base_url", "model"):
            _require(str(p.get(f, "")).strip(), "INVALID_PROVIDERS", f"provider 缺少必填字段 {f}")
        _require(p["name"] not in names, "INVALID_PROVIDERS", f"provider 名称重复：{p['name']}")
        names.append(p["name"])
        _require(p.get("key_env", "") == "" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p["key_env"]),
                 "INVALID_PROVIDERS", f"key_env 不是合法环境变量名：{p.get('key_env')}")
    _require(default in names, "INVALID_PROVIDERS", f"default 必须是已有 provider：{default}")
    _write_config("providers.json", {"providers": providers, "default": default})
    audit_mod.audit("settings.providers.update", detail={"count": len(providers), "default": default})
    return settings_view()


@router.put("/settings/complexity-map")
def update_complexity_map(body: dict):
    levels = body.get("levels")
    _require(isinstance(levels, dict) and levels, "INVALID_COMPLEXITY", "levels 必须为非空对象")
    for k, v in levels.items():
        mods = (v or {}).get("modules")
        _require(mods == "*all*" or (isinstance(mods, list) and all(isinstance(m, str) for m in mods)),
                 "INVALID_COMPLEXITY", f"{k}.modules 必须为模块数组或 '*all*'")
    _write_config("complexity-map.json", {"levels": levels})
    audit_mod.audit("settings.complexity.update")
    return settings_view()


@router.put("/settings/sanitize-rules")
def update_sanitize_rules(body: dict):
    rules = body.get("rules")
    _require(isinstance(rules, list), "INVALID_SANITIZE", "rules 必须为数组")
    seen: set[str] = set()
    for r in rules:
        _require(isinstance(r, dict) and str(r.get("id", "")).strip()
                 and str(r.get("pattern", "")).strip(), "INVALID_SANITIZE", "规则需要 id 与 pattern")
        _require(r["id"] not in seen, "INVALID_SANITIZE", f"规则 id 重复：{r['id']}")
        seen.add(r["id"])
        try:
            re.compile(r["pattern"])
        except re.error as e:
            raise ApiError("INVALID_SANITIZE", 400, f"正则编译失败 {r['id']}：{e}")
        _require(r.get("severity") in ("block", "confirm"), "INVALID_SANITIZE",
                 f"severity 只能为 block/confirm：{r.get('severity')}")
    _write_config("sanitize-rules.json", {"rules": rules})
    audit_mod.audit("settings.sanitize.update", detail={"count": len(rules)})
    return settings_view()


@router.get("/settings/constraints-package")
def get_constraints_package():
    path = paths.config_root() / "constraints-package.md"
    return {"content": path.read_text(encoding="utf-8") if path.exists() else ""}


@router.put("/settings/constraints-package")
def update_constraints_package(body: dict):
    content = str(body.get("content", ""))
    _require(content.strip(), "INVALID_CONSTRAINTS", "内容不能为空（硬约束包不可清空）")
    _require("package_version:" in content, "INVALID_CONSTRAINTS",
             "front matter 中必须保留 package_version")
    _write_config_text("constraints-package.md", content)
    audit_mod.audit("settings.constraints.update")
    return settings_view()


@router.get("/audit")
def audit_tail(limit: int = 200):
    return {"entries": audit_mod.read_audit(limit)}


@router.get("/backups")
def backups():
    return {"backups": backup.list_backups()}


@router.post("/backups", status_code=202)
def run_backup():
    result = backup.snapshot()
    return result


@router.post("/backups/restore")
def restore_backup(body: dict):
    date = str(body.get("date", ""))
    confirm = str(body.get("confirm_name", ""))
    result = backup.restore(date, confirm)
    return result


@router.get("/events")
async def events(request: Request):
    last_id = request.headers.get("Last-Event-ID")
    last_id_int = int(last_id) if last_id and last_id.isdigit() else None
    return StreamingResponse(subscribe(last_id_int), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
