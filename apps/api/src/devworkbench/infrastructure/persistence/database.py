from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from devworkbench.shared.paths import data_root

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")

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

def database() -> Database:
    return Database(data_root() / "devworkbench-v2.sqlite3")
