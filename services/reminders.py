"""Refill reminders: remind_date = last_date + days_supply - 7.

Daily sweeper (EventBridge -> scripts/sweeper.py) pings only when due
(remind_date <= today and not yet notified) via Telegram, else console.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import requests

from agent.models import Reminder
from services import db


def compute_remind_date(last_date: str, days_supply: int) -> str:
    base = dt.date.fromisoformat(last_date)
    return (base + dt.timedelta(days=days_supply - 7)).isoformat()


def set_reminder(user_id: str, drug: str, days_supply: int, last_date: str) -> Reminder:
    remind_date = compute_remind_date(last_date, days_supply)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (user_id, drug, days_supply, last_date, remind_date)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, drug.strip(), int(days_supply), last_date, remind_date),
        )
        rid = cur.lastrowid
    return Reminder(
        id=rid, user_id=user_id, drug=drug.strip(),
        days_supply=int(days_supply), last_date=last_date, remind_date=remind_date,
    )


def list_reminders(user_id: str | None = None) -> list[Reminder]:
    with db.connect() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE user_id = ? ORDER BY remind_date", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM reminders ORDER BY remind_date").fetchall()
    return [
        Reminder(
            id=r["id"], user_id=r["user_id"], drug=r["drug"],
            days_supply=r["days_supply"], last_date=r["last_date"],
            remind_date=r["remind_date"],
        )
        for r in rows
    ]


def delete_reminder(reminder_id: int) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    return cur.rowcount > 0


def clear_user_data(user_id: str) -> int:
    """Delete-button path: wipe reminders (+ nothing else is stored per user)."""
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
    return cur.rowcount


def due_reminders(today: str | None = None) -> list[Reminder]:
    today = today or dt.date.today().isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE remind_date <= ? AND notified = 0"
            " ORDER BY remind_date",
            (today,),
        ).fetchall()
    return [
        Reminder(
            id=r["id"], user_id=r["user_id"], drug=r["drug"],
            days_supply=r["days_supply"], last_date=r["last_date"],
            remind_date=r["remind_date"],
        )
        for r in rows
    ]


def mark_notified(reminder_id: int) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,))


def send_ping(text: str) -> str:
    """Telegram if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID set, else console. Returns channel."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        ).raise_for_status()
        return "telegram"
    print(f"[agentRx reminder] {text}")
    return "console"


def sweep(today: str | None = None) -> list[dict]:
    """Ping once per due reminder. Returns list of {reminder, channel}."""
    out = []
    for r in due_reminders(today):
        channel = send_ping(
            f"agentRx refill reminder: {r.drug} is due for refill "
            f"(remind date {r.remind_date}). Info only, confirm with pharmacist/doctor."
        )
        if r.id is not None:
            mark_notified(r.id)
        out.append({"reminder": r.to_dict(), "channel": channel})
    return out


def save_run(drug: str, strength: str, qty: int, lat: float, lng: float, result_json: dict) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO search_runs (drug, strength, qty, lat, lng, result_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (drug, strength, qty, lat, lng, json.dumps(result_json)),
        )
        return cur.lastrowid
