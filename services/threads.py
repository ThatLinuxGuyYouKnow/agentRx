"""Thread persistence: long-running back-and-forths.

History lives in SQLite (threads/messages tables — same shape a DynamoDB
port wants later). Each turn rebuilds the Strands agent with full history.
"""

from __future__ import annotations

from typing import Any

from services import db


def create_thread(org_id: str = "demo-org", title: str = "conversation") -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO threads (org_id, title) VALUES (?, ?)", (org_id, title)
        )
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_threads(org_id: str = "demo-org") -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM threads WHERE org_id = ? ORDER BY id DESC", (org_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(thread_id: int, role: str, content: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO messages (thread_id, role, content) VALUES (?, ?, ?)",
            (thread_id, role, content),
        )


def get_messages(thread_id: int) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def strands_history(thread_id: int, limit: int = 40) -> list[dict[str, Any]]:
    """History in Strands message format, oldest-first, capped."""
    msgs = get_messages(thread_id)[-limit:]
    return [{"role": m["role"], "content": [{"text": m["content"]}]} for m in msgs]
