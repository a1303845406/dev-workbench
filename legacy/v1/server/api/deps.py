"""Shared API helpers: outbound confirmation check (G-10), request id."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request

from ..core.errors import ApiError, outbound_confirm_required


def request_id() -> str:
    return f"req-{uuid.uuid4().hex[:8]}"


def require_outbound(body: dict) -> dict:
    """G-10/NFR-12: any model call must carry outbound_confirmation (428 otherwise)."""
    conf = body.get("outbound_confirmation")
    if not isinstance(conf, dict) or not conf.get("provider") or not conf.get("confirmed_at"):
        raise outbound_confirm_required()
    return conf


def client_window(request: Request) -> str | None:
    return request.headers.get("X-Client-Window")


def error_payload(code: str, message: str, detail: Any = None) -> dict:
    from datetime import datetime
    return {"error": {"code": code, "message": message, "detail": detail,
                      "request_id": request_id(),
                      "ts": datetime.now().astimezone().isoformat(timespec="seconds")}}
