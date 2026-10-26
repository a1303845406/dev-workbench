"""Backup scheduler (NFR-08): daily snapshot of projects/ with key pre-scan,
30-day retention, manual trigger, startup catch-up, SSE notification."""
from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import paths
from .audit import audit
from .sanitize import scan as sanitize_scan
from .persist import atomic_write_json, now_iso

RETENTION_DAYS = 30
SCHEDULE_HM = (2, 30)


def snapshot(label: str | None = None) -> dict:
    src = paths.projects_root()
    date = label or datetime.now().strftime("%Y-%m-%d")
    dst = paths.backup_root() / date
    dst.mkdir(parents=True, exist_ok=True)
    files = 0
    skipped_key_hits = 0
    if src.exists():
        for f in src.rglob("*"):
            if not f.is_file() or f.suffix == ".lock" or ".tmp" in f.name:
                continue
            try:
                text = f.read_text(encoding="utf-8")
                if sanitize_scan(text):
                    skipped_key_hits += 1
                    continue
            except (UnicodeDecodeError, OSError):
                pass
            rel = f.relative_to(src)
            target = dst / "projects" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            files += 1
    atomic_write_json(dst / "backup-meta.json",
                      {"date": date, "files": files, "skipped_key_hits": skipped_key_hits,
                       "ts": now_iso()}, schema_version=None)
    audit("backup.run", detail={"date": date, "files": files, "skipped_key_hits": skipped_key_hits})
    return {"date": date, "files": files, "skipped_key_hits": skipped_key_hits}


def list_backups() -> list[dict]:
    root = paths.backup_root()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        meta = d / "backup-meta.json"
        if d.is_dir() and meta.exists():
            import json
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                out.append({"date": d.name})
    return out


def restore(date: str, confirm_name: str) -> dict:
    """Restore projects/ from a snapshot; auto-snapshot current state first (S-01)."""
    from .errors import ApiError
    if confirm_name != date:
        raise ApiError("RESTORE_CONFIRM_MISSING", 400, "confirm_name 与快照日期不一致")
    src = paths.backup_root() / date / "projects"
    if not src.is_dir():
        raise ApiError("BACKUP_NOT_FOUND", 404, "快照不存在", {"date": date})
    pre = snapshot(label=f"{datetime.now().strftime('%Y-%m-%d')}-pre-restore")
    dst = paths.projects_root()
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    files = sum(1 for f in dst.rglob("*") if f.is_file())
    audit("backup.restore", detail={"date": date, "files": files, "pre_snapshot": pre["date"]})
    return {"restored": date, "files": files, "pre_restore_snapshot": pre["date"]}


def cleanup_old(keep_days: int = RETENTION_DAYS) -> list[str]:
    root = paths.backup_root()
    removed = []
    if not root.exists():
        return removed
    cutoff = time.time() - keep_days * 86400
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d)
                removed.append(d.name)
        except OSError:
            continue
    return removed


async def scheduler_loop() -> None:
    """Daily loop with startup catch-up (missed today's run → run within 5 min)."""
    from . import events
    ran_today = {b.get("date") for b in list_backups()}
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in ran_today:
        await asyncio.sleep(5 * 60 if datetime.now().hour >= SCHEDULE_HM[0] else 60)
        if datetime.now().strftime("%Y-%m-%d") not in {b.get("date") for b in list_backups()}:
            result = snapshot()
            await events.publish("backup.completed", result)
    while True:
        now = datetime.now()
        target = now.replace(hour=SCHEDULE_HM[0], minute=SCHEDULE_HM[1], second=0, microsecond=0)
        if now >= target:
            target = target + _one_day()
        wait_s = max(1.0, (target - now).total_seconds())
        await asyncio.sleep(wait_s)
        result = snapshot()
        cleanup_old()
        await events.publish("backup.completed", result)


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)
