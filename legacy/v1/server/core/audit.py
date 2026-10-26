"""Structured JSONL audit log (NFR-09 / S-05). API keys are erased before write."""
from __future__ import annotations

import json
import re
import socket
from typing import Any

from . import paths
from .persist import atomic_write_text, now_iso

_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)(\s*[:=]\s*)(\S+)"),
]


def erase_keys(text: str) -> str:
    for pat in _KEY_PATTERNS:
        text = pat.sub(lambda m: m.group(0)[:6] + "***", text)
    return text


def audit(action: str, *, project: str = "", target: str = "", source_ip: str = "127.0.0.1",
          detail: Any = None) -> None:
    entry = {
        "ts": now_iso(),
        "action": action,
        "project": project,
        "target": target,
        "source_ip": source_ip,
        "hostname": socket.gethostname(),
        "detail": detail,
    }
    text = json.dumps(entry, ensure_ascii=False, default=str)
    text = erase_keys(text)
    path = paths.audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def read_audit(limit: int = 200) -> list[dict]:
    path = paths.audit_log_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
