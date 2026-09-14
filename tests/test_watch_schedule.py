"""Per-drug re-check cadence ("we run out every 5 days") tests."""

import datetime as dt
import os
import sqlite3

os.environ.setdefault("AGENTRX_DB_PATH", "/tmp/opencode/agentrx_sched_test.db")

from services import db  # noqa: E402
import services.watchlist as watch_svc  # noqa: E402

TEST_DB = "/tmp/opencode/agentrx_sched_test.db"


def setup_function(_):
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.DEFAULT_DB_PATH = TEST_DB


def test_add_watch_defaults_and_clamps_cadence():
    w = watch_svc.add_watch("org1", "paracetamol", "50mg")
    assert w["check_every_days"] == 7
    assert watch_svc.add_watch("org1", "a", check_every_days=5)["check_every_days"] == 5
    assert watch_svc.add_watch("org1", "b", check_every_days=0)["check_every_days"] == 1
    assert watch_svc.add_watch("org1", "c", check_every_days=999)["check_every_days"] == 90


def _backdate_snapshot(watch_id: int, days_ago: int):
    old = (dt.date.today() - dt.timedelta(days=days_ago)).isoformat() + "T09:00:00"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (watch_id, checked_at, result_json) VALUES (?, ?, ?)",
            (watch_id, old, '{"drug": "x", "results": []}'),
        )


def test_board_staleness_follows_per_drug_cadence():
    w5 = watch_svc.add_watch("org1", "often", check_every_days=5)
    w30 = watch_svc.add_watch("org1", "rarely", check_every_days=30)
    _backdate_snapshot(w5["id"], 6)   # older than 5 -> stale
    _backdate_snapshot(w30["id"], 6)  # younger than 30 -> fresh
    rows = {r["watch"]["drug"]: r for r in watch_svc.board("org1")}
    assert rows["often"]["stale"] is True
    assert rows["rarely"]["stale"] is False
    assert rows["rarely"]["interval_days"] == 30
    assert rows["rarely"]["due_in_days"] == 24
    assert rows["rarely"]["next_check"] == (
        dt.date.today() + dt.timedelta(days=24)).isoformat()
    # never-checked watch is due immediately
    w_new = watch_svc.add_watch("org1", "fresh-watch", check_every_days=5)
    row_new = {r["watch"]["drug"]: r for r in watch_svc.board("org1")}["fresh-watch"]
    assert row_new["stale"] is True and row_new["next_check"] is None
    assert w_new["check_every_days"] == 5


def test_update_watch_cadence_and_api():
    from fastapi.testclient import TestClient

    from server import app

    c = TestClient(app)
    w = c.post("/api/watchlist", json={"org_id": "sched",
                                       "drug": "paracetamol",
                                       "check_every_days": 5}).json()
    assert w["check_every_days"] == 5
    upd = c.patch(f"/api/watchlist/{w['id']}?org_id=sched",
                  json={"check_every_days": 14}).json()
    assert upd["check_every_days"] == 14
    board = c.get("/api/board", params={"org_id": "sched"}).json()
    assert board[0]["interval_days"] == 14
    assert c.patch("/api/watchlist/9999?org_id=sched",
                   json={"check_every_days": 5}).status_code == 404


def test_offline_coordinator_parses_every_n_days():
    from agent import coordinator

    g = coordinator.build_graph("org1", 40.7, -74.0, live=False)
    reply = coordinator._text_of(g["coordinator"]("watch paracetamol 50mg every 5 days"))
    assert "every 5 days" in reply
    watches = [w for w in
               __import__("services.watchlist", fromlist=["list_watches"]).list_watches("org1")]
    hit = [w for w in watches if w["drug"] == "paracetamol"]
    assert hit and hit[0]["check_every_days"] == 5 and hit[0]["strength"] == "50mg"


def test_migration_adds_column_to_legacy_db():
    legacy = "/tmp/opencode/agentrx_legacy_test.db"
    if os.path.exists(legacy):
        os.remove(legacy)
    conn = sqlite3.connect(legacy)
    conn.execute(
        "CREATE TABLE watched (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " org_id TEXT NOT NULL DEFAULT 'demo-org', drug TEXT NOT NULL,"
        " strength TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL DEFAULT 30,"
        " radius_km REAL NOT NULL DEFAULT 5, delivery INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute("INSERT INTO watched (org_id, drug) VALUES ('o', 'legacy-drug')")
    conn.commit()
    conn.close()
    db.DEFAULT_DB_PATH = legacy
    try:
        watches = watch_svc.list_watches("o")
        assert watches[0]["check_every_days"] == 7  # backfilled default
        assert watch_svc.board("o")[0]["interval_days"] == 7
    finally:
        db.DEFAULT_DB_PATH = TEST_DB
