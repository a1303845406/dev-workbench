"""Path layout resolution (env-overridable; data root default ./workspace)."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(env: str, default: Path) -> Path:
    v = os.environ.get(env)
    return Path(v).expanduser().resolve() if v else default


def data_root() -> Path:
    return _env_path("DEVWB_HOME", REPO_ROOT / "workspace")


def config_root() -> Path:
    return _env_path("DEVWB_CONFIG_DIR", REPO_ROOT / "configs")


def backup_root() -> Path:
    return _env_path("DEVWB_BACKUP_DIR", REPO_ROOT / "backups")


def projects_root() -> Path:
    return data_root() / "projects"


def project_dir(project: str) -> Path:
    return projects_root() / project


def logs_dir() -> Path:
    return data_root() / "logs"


def audit_log_path() -> Path:
    return logs_dir() / "audit.jsonl"


def static_dir() -> Path:
    v = os.environ.get("DEVWB_STATIC_DIR")
    if v:
        return Path(v).resolve()
    return REPO_ROOT / "frontend" / "dist"


def frontend_dist_exists() -> bool:
    return (static_dir() / "index.html").is_file()
