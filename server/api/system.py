"""System endpoints: health/version/settings/backups/events(SSE)/audit."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.audit import audit as audit_mod
from ..core import backup, paths
from ..core.config import config_center
from ..core.events import subscribe
from ..core.settings import settings_view

router = APIRouter()

VERSION = "1.0.0"


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


@router.get("/events")
async def events(request: Request):
    last_id = request.headers.get("Last-Event-ID")
    last_id_int = int(last_id) if last_id and last_id.isdigit() else None
    return StreamingResponse(subscribe(last_id_int), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
