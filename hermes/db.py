"""SQLite persistence: task queue, activity log, and long-term memory notes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | in_progress | done | failed
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    entry TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def connect() -> sqlite3.Connection:
    config.ensure_data_dir()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# --- Tasks ---------------------------------------------------------------

def add_task(description: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (description, status, created_at, updated_at) VALUES (?, 'pending', ?, ?)",
            (description, _now(), _now()),
        )
        return cur.lastrowid


def get_task(task_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def next_pending_task() -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()


def list_tasks(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def recent_completed_tasks(limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE status IN ('done', 'failed') ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def set_task_status(task_id: int, status: str, result: str | None = None) -> None:
    with connect() as conn:
        if result is None:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, result, _now(), task_id),
            )


# --- Activity log --------------------------------------------------------

def add_log(entry: str, task_id: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO log (task_id, entry, created_at) VALUES (?, ?, ?)",
            (task_id, entry, _now()),
        )


def get_log(task_id: int | None = None, limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        if task_id is None:
            return conn.execute(
                "SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM log WHERE task_id = ? ORDER BY id LIMIT ?",
            (task_id, limit),
        ).fetchall()


# --- Memory notes --------------------------------------------------------

def save_memory(key: str, content: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO memory (key, content, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
            """,
            (key, content, _now()),
        )


def get_memory(key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM memory WHERE key = ?", (key,)).fetchone()


def delete_memory(key: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM memory WHERE key = ?", (key,))
        return cur.rowcount > 0


def list_memories() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM memory ORDER BY key").fetchall()
