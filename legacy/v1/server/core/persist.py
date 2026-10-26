"""Atomic persistence: temp-file + os.replace writes, .bak rolling copies,
front matter (subset YAML) parse/serialize, safe-name validation, light locks.

总体设计 6.1 / 数据设计 §1 / FR-06 P0。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from .errors import ApiError, schema_version_unsupported

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5][A-Za-z0-9_\-\u4e00-\u9fa5 .]{0,80}$")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def check_safe_name(name: str, what: str = "名称") -> str:
    if not name or not SAFE_NAME_RE.match(name.strip()) or name.strip() in {".", ".."}:
        raise ApiError("INVALID_NAME", 400, f"{what}含非法字符或为空（允许中文/字母/数字/_-/空格）",
                       {"got": name})
    return name.strip()


# ---------------------------------------------------------------- atomic write

def atomic_write_text(path: Path, text: str, *, keep_bak: bool = True) -> None:
    """Write UTF-8 text atomically; keep up to 3 rolling .bak copies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if keep_bak and path.exists():
        for i in (2, 1):
            src = path.with_suffix(path.suffix + (".bak" if i == 1 else f".bak{i}"))
            dst = path.with_suffix(path.suffix + (f".bak{i+1}" if i + 1 <= 3 else ".bak3"))
            if src.exists():
                os.replace(src, dst)
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            os.replace(path, bak)
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}{int(time.time()*1000)%100000}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: dict, *, schema_version: int | None = 1) -> None:
    if schema_version is not None:
        obj = {"schemaVersion": schema_version, **{k: v for k, v in obj.items() if k != "schemaVersion"}}
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ------------------------------------------------------------------- read side

def _bak_candidates(path: Path) -> list[Path]:
    return [path.with_suffix(path.suffix + s) for s in (".bak", ".bak1", ".bak2", ".bak3")]


def load_json(path: Path, *, default: dict | None = None, max_schema: int = 1) -> dict:
    """Load JSON; on parse failure fall back to .bak copies; None → default or {}."""
    candidates = [path] + _bak_candidates(path)
    for cand in candidates:
        if not cand.exists():
            continue
        try:
            obj = json.loads(cand.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sv = obj.get("schemaVersion", 1)
        if isinstance(sv, int) and sv > max_schema:
            raise schema_version_unsupported(str(cand), sv)
        return obj
    if default is not None:
        return default
    raise ApiError("DATA_NOT_FOUND", 404, "数据文件不存在且无完好副本", {"path": str(path)})


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def exists(path: Path) -> bool:
    return path.exists()


# ------------------------------------------------------------------ front matter

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Parse a minimal YAML-ish front matter: `key: value` lines, scalar values,
    inline JSON lists/objects. Returns (meta, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for raw_line in m.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        meta[key] = _parse_scalar(val)
    return meta, text[m.end():]


def _parse_scalar(val: str):
    if val == "":
        return ""
    if (val.startswith("[") and val.endswith("]")) or (val.startswith("{") and val.endswith("}")):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    if val.startswith("*") or val in ("true", "True"):
        return True if val in ("true", "True") else val
    if val in ("false", "False"):
        return False
    if re.fullmatch(r"-?\d+(\.\d+)?", val):
        return float(val) if "." in val else int(val)
    return val.strip("'\"")


def with_front_matter(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (list, dict)):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {v}")
    lines += ["---", ""]
    return "\n".join(lines) + body


# ------------------------------------------------------------------- file locks

def check_lock(path: Path, client_window: str | None) -> dict | None:
    """Light lock: returns holder info if another window holds a lock, else None."""
    lock = path.with_suffix(path.suffix + ".lock")
    if not lock.exists():
        return None
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if client_window and data.get("window") == client_window:
        return None
    if time.time() - data.get("ts", 0) > 3600:  # stale lock → ignore
        return None
    return {"holder_window": data.get("window"), "ts": data.get("iso")}


def acquire_lock(path: Path, client_window: str | None) -> None:
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(lock, {"window": client_window or "unknown", "ts": time.time(), "iso": now_iso()},
                      schema_version=None)


def release_lock(path: Path, client_window: str | None) -> None:
    lock = path.with_suffix(path.suffix + ".lock")
    if lock.exists():
        try:
            lock.unlink()
        except OSError:
            pass
