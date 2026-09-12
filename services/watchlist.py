"""Watchlist: org's basket of watched drugs + check snapshots for the board."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from services import db

STALE_AFTER_DAYS = 7


def add_watch(org_id: str, drug: str, strength: str = "", qty: int = 30,
              radius_km: float = 5, delivery: bool = False) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO watched (org_id, drug, strength, qty, radius_km, delivery)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (org_id, drug.strip(), strength.strip(), int(qty), float(radius_km), int(delivery)),
        )
        wid = cur.lastrowid
        row = conn.execute("SELECT * FROM watched WHERE id = ?", (wid,)).fetchone()
    return dict(row)


def remove_watch(watch_id: int, org_id: str) -> bool:
    with db.connect() as conn:
        conn.execute("DELETE FROM snapshots WHERE watch_id = ?", (watch_id,))
        cur = conn.execute("DELETE FROM watched WHERE id = ? AND org_id = ?", (watch_id, org_id))
    return cur.rowcount > 0


def list_watches(org_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watched WHERE org_id = ? ORDER BY drug", (org_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_snapshot(watch_id: int, result: dict[str, Any]) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (watch_id, result_json) VALUES (?, ?)",
            (watch_id, json.dumps(result)),
        )
        return cur.lastrowid


def latest_snapshot(watch_id: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE watch_id = ? ORDER BY id DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
    if not row:
        return None
    return {"checked_at": row["checked_at"], "result": json.loads(row["result_json"])}


def board(org_id: str, today: str | None = None) -> list[dict[str, Any]]:
    """One row per watched drug: latest snapshot + stale flag. No calls placed."""
    today_d = dt.date.fromisoformat(today) if today else dt.date.today()
    out = []
    for w in list_watches(org_id):
        snap = latest_snapshot(w["id"])
        stale = True
        if snap:
            age = (today_d - dt.date.fromisoformat(snap["checked_at"][:10])).days
            stale = age > STALE_AFTER_DAYS
        out.append({"watch": w, "snapshot": snap, "stale": stale})
    return out


def stale_watches(org_id: str) -> list[dict[str, Any]]:
    return [row for row in board(org_id) if row["stale"]]
