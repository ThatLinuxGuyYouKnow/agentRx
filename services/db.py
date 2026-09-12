"""SQLite local store. Minimal data only: drug name + reminder date. No Rx numbers, no PHI."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any

DEFAULT_DB_PATH = os.environ.get(
    "AGENTRX_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "agentrx.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    drug TEXT NOT NULL,
    strength TEXT NOT NULL,
    qty INTEGER NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    drug TEXT NOT NULL,
    days_supply INTEGER NOT NULL,
    last_date TEXT NOT NULL,
    remind_date TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL DEFAULT 'demo-org',
    title TEXT NOT NULL DEFAULT 'conversation',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES threads(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS watched (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL DEFAULT 'demo-org',
    drug TEXT NOT NULL,
    strength TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL DEFAULT 30,
    radius_km REAL NOT NULL DEFAULT 5,
    delivery INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watched(id),
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    result_json TEXT NOT NULL DEFAULT '{}'
);
"""


def get_db_path() -> str:
    return os.path.abspath(DEFAULT_DB_PATH)


@contextmanager
def connect(db_path: str | None = None):
    path = os.path.abspath(db_path or get_db_path())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def dict_rows(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
