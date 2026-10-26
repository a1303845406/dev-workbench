from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'active', workspace_path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repositories (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS code_baselines (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE, branch TEXT NOT NULL, commit_sha TEXT NOT NULL, workspace_path TEXT NOT NULL, manifest_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, kind TEXT NOT NULL, title TEXT NOT NULL, current_version_id TEXT, created_at TEXT NOT NULL, UNIQUE(project_id, kind, title));
CREATE TABLE IF NOT EXISTS artifact_versions (id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE, version TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', based_on_version_id TEXT, created_at TEXT NOT NULL, UNIQUE(artifact_id, version));
CREATE TABLE IF NOT EXISTS modules (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, parent_id TEXT REFERENCES modules(id), name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, UNIQUE(project_id, parent_id, name));
CREATE TABLE IF NOT EXISTS features (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned', created_at TEXT NOT NULL, UNIQUE(module_id, name));
CREATE TABLE IF NOT EXISTS task_cards (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, feature_id TEXT NOT NULL REFERENCES features(id) ON DELETE CASCADE, kind TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', plan_status TEXT NOT NULL DEFAULT 'draft', ai_review_status TEXT NOT NULL DEFAULT 'pending', ai_review_reason TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT '', artifact_refs_json TEXT NOT NULL DEFAULT '[]', supersedes_task_id TEXT REFERENCES task_cards(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompt_instances (id TEXT PRIMARY KEY, task_card_id TEXT NOT NULL REFERENCES task_cards(id) ON DELETE CASCADE, content TEXT NOT NULL, context_json TEXT NOT NULL DEFAULT '{}', provider_mode TEXT NOT NULL DEFAULT 'deterministic', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, task_card_id TEXT NOT NULL REFERENCES task_cards(id) ON DELETE CASCADE, prompt_instance_id TEXT NOT NULL REFERENCES prompt_instances(id), baseline_id TEXT NOT NULL REFERENCES code_baselines(id), status TEXT NOT NULL, mcp_token_hash TEXT NOT NULL, started_at TEXT, ended_at TEXT, machine_checks_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_receipts (id TEXT PRIMARY KEY, report_id TEXT NOT NULL UNIQUE, execution_id TEXT NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE, report_type TEXT NOT NULL, payload_json TEXT NOT NULL, changed_files_json TEXT NOT NULL DEFAULT '[]', submitted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, execution_id TEXT REFERENCES execution_runs(id), command TEXT NOT NULL, status TEXT NOT NULL, exit_code INTEGER, stdout_path TEXT NOT NULL DEFAULT '', report_json TEXT NOT NULL DEFAULT '{}', started_at TEXT NOT NULL, ended_at TEXT);
CREATE TABLE IF NOT EXISTS conclusions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, execution_id TEXT REFERENCES execution_runs(id), kind TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, risks_json TEXT NOT NULL DEFAULT '[]', evidence_json TEXT NOT NULL DEFAULT '[]', actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, target_type TEXT NOT NULL, target_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_runs_task ON execution_runs(task_card_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tests_project ON test_runs(project_id, started_at DESC);
CREATE TABLE IF NOT EXISTS skill_assets (id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, version TEXT NOT NULL DEFAULT "1.0", enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(project_id, name));
CREATE TABLE IF NOT EXISTS task_splits (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, source_task_id TEXT NOT NULL REFERENCES task_cards(id) ON DELETE CASCADE, provider_mode TEXT NOT NULL, skills_json TEXT NOT NULL DEFAULT "[]", proposal_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def jload(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default

class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn
    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            repository_columns = {item[1] for item in conn.execute("PRAGMA table_info(repositories)").fetchall()}
            if "name" not in repository_columns:
                conn.execute("ALTER TABLE repositories ADD COLUMN name TEXT NOT NULL DEFAULT '主仓库'")
            conn.execute("UPDATE repositories SET name='主仓库' WHERE name IS NULL OR TRIM(name)=''")
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    def one(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(sql, args).fetchone()
    def all(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(sql, args).fetchall()

def data_root() -> Path:
    return Path(os.environ.get("DEVWB_HOME", "./workspace")).resolve()

def database() -> Database:
    return Database(data_root() / "devworkbench-v2.sqlite3")
