"""Reminder math + due-only sweeper tests."""

import datetime as dt

from services import db
from services.reminders import (
    clear_user_data,
    compute_remind_date,
    delete_reminder,
    due_reminders,
    set_reminder,
    sweep,
)

TEST_DB = "/tmp/opencode/agentrx_test.db"


def _iso(days_from_today: int) -> str:
    return (dt.date.today() + dt.timedelta(days=days_from_today)).isoformat()


def setup_function(_):
    import os

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.DEFAULT_DB_PATH = TEST_DB


def test_remind_date_math():
    # last + supply - 7
    assert compute_remind_date("2026-08-01", 30) == "2026-08-24"


def test_sweep_pings_only_when_due():
    set_reminder("u1", "atorvastatin", 30, _iso(-30))  # remind was 7 days ago -> due
    set_reminder("u1", "lisinopril", 30, _iso(0))  # remind in 23 days -> not due
    due = due_reminders()
    assert len(due) == 1 and due[0].drug == "atorvastatin"
    out = sweep()
    assert len(out) == 1 and out[0]["channel"] == "console"
    assert due_reminders() == []  # notified once
    assert delete_reminder(out[0]["reminder"]["id"])
    assert clear_user_data("u1") == 1
