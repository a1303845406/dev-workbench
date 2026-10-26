"""Read-only settings aggregation (S-02 / AC-11): current effective config, masked."""
from __future__ import annotations

from pathlib import Path

from . import paths
from .backup import list_backups
from .config import config_center
from .constraints import conflict_keywords, package_version


def settings_view() -> dict:
    providers = config_center.providers()
    masked = []
    for p in providers.get("providers", []):
        masked.append({"name": p.get("name"), "base_url": p.get("base_url"),
                       "model": p.get("model"), "key_env": p.get("key_env"),
                       "key": "***", "stream": p.get("stream", True)})
    cmap = config_center.complexity_map()
    cmeta, cbody = config_center.constraints_package()
    return {
        "providers": masked,
        "default_provider": providers.get("default"),
        "templates": [{"id": t, **{k: v for k, v in (config_center.template(t) or {}).items()
                                    if k != "schemaVersion"}} for t in config_center.template_dirs()],
        "complexity_map": cmap,
        "constraints_package": {"package_version": package_version(),
                                "conflict_keywords": conflict_keywords(),
                                "preview": cbody[:600]},
        "sanitize_rules": config_center.sanitize_rules(),
        "backup": {"dir": str(paths.backup_root()), "schedule": "每日 02:30",
                   "retention_days": 30, "recent": list_backups()[:10]},
        "paths": {"data_root": str(paths.data_root()), "config_root": str(paths.config_root()),
                  "audit_log": str(paths.audit_log_path())},
        "degraded": config_center.degraded_configs(),
    }
