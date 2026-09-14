"""Watchlist: org's basket of watched drugs + check snapshots for the board.

Each watch carries its own re-check cadence (``check_every_days`` — e.g.
"paracetamol usually runs out every 5 days, check that often"). Staleness
is per-drug: a watch is stale when never checked or when its last check
is older than its cadence. Schedules drive re-check *proposals* only —
calls still wait for human approval in the board drawer.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from services import db

STALE_AFTER_DAYS = 7  # fallback when a watch has no usable cadence
MIN_EVERY_DAYS = 1
MAX_EVERY_DAYS = 90


def clamp_every_days(n: Any) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        return STALE_AFTER_DAYS
    return max(MIN_EVERY_DAYS, min(MAX_EVERY_DAYS, v))


def _interval_of(watch: dict[str, Any]) -> int:
    return clamp_every_days(watch.get("check_every_days", STALE_AFTER_DAYS))


def add_watch(org_id: str, drug: str, strength: str = "", qty: int = 30,
              radius_km: float = 5, delivery: bool = False,
              check_every_days: int = STALE_AFTER_DAYS) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO watched (org_id, drug, strength, qty, radius_km, delivery,"
            " check_every_days) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (org_id, drug.strip(), strength.strip(), int(qty), float(radius_km),
             int(delivery), clamp_every_days(check_every_days)),
        )
        wid = cur.lastrowid
        row = conn.execute("SELECT * FROM watched WHERE id = ?", (wid,)).fetchone()
    return dict(row)


def update_watch(watch_id: int, org_id: str,
                 check_every_days: int | None = None) -> dict[str, Any] | None:
    """Edit a watch's re-check cadence. Returns the updated watch or None."""
    if check_every_days is None:
        return None
    with db.connect() as conn:
        conn.execute(
            "UPDATE watched SET check_every_days = ? WHERE id = ? AND org_id = ?",
            (clamp_every_days(check_every_days), watch_id, org_id),
        )
        row = conn.execute(
            "SELECT * FROM watched WHERE id = ? AND org_id = ?", (watch_id, org_id)
        ).fetchone()
    return dict(row) if row else None


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
    """One row per watched drug: latest snapshot + cadence-aware stale flag.

    Extra row keys: ``interval_days``, ``next_check`` (YYYY-MM-DD or None
    when never checked), ``due_in_days`` (None when never checked,
    negative when overdue). No calls placed.
    """
    today_d = dt.date.fromisoformat(today) if today else dt.date.today()
    out = []
    for w in list_watches(org_id):
        interval = _interval_of(w)
        snap = latest_snapshot(w["id"])
        stale = True
        next_check: str | None = None
        due_in: int | None = None
        if snap:
            checked = dt.date.fromisoformat(snap["checked_at"][:10])
            age = (today_d - checked).days
            stale = age > interval
            nxt = checked + dt.timedelta(days=interval)
            next_check = nxt.isoformat()
            due_in = (nxt - today_d).days
        out.append({"watch": w, "snapshot": snap, "stale": stale,
                    "interval_days": interval, "next_check": next_check,
                    "due_in_days": due_in})
    return out


def stale_watches(org_id: str) -> list[dict[str, Any]]:
    return [row for row in board(org_id) if row["stale"]]
