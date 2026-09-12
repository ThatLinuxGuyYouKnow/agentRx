"""Threads (memory) + watchlist/board tests."""

import os

os.environ.setdefault("AGENTRX_DB_PATH", "/tmp/opencode/agentrx_threads_test.db")

import services.threads as thread_svc  # noqa: E402
import services.watchlist as watch_svc  # noqa: E402
from services import db  # noqa: E402

TEST_DB = "/tmp/opencode/agentrx_threads_test.db"


def setup_function(_):
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.DEFAULT_DB_PATH = TEST_DB


def test_thread_roundtrip_and_history_shape():
    t = thread_svc.create_thread("org1", "intake")
    thread_svc.add_message(t["id"], "user", "watch atorvastatin")
    thread_svc.add_message(t["id"], "assistant", "done")
    msgs = thread_svc.get_messages(t["id"])
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "watch atorvastatin"), ("assistant", "done")]
    hist = thread_svc.strands_history(t["id"])
    assert hist[0] == {"role": "user", "content": [{"text": "watch atorvastatin"}]}
    assert any(x["org_id"] == "org1" for x in thread_svc.list_threads("org1"))


def test_run_turn_uses_history_and_persists(monkeypatch):
    import agent.assistant as assistant

    seen = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            seen["history_len"] = len(kwargs.get("messages", []))

        def __call__(self, text):
            seen["prompt"] = text
            return type("R", (), {"message": {"content": [{"text": "ack: " + text}]}})()

    monkeypatch.setattr(assistant, "build_thread_agent",
                        lambda org, lat, lng, hist: FakeAgent(messages=hist))
    t = thread_svc.create_thread("org1")
    thread_svc.add_message(t["id"], "user", "earlier")
    thread_svc.add_message(t["id"], "assistant", "noted")
    reply = assistant.run_turn(t["id"], "org1", "hello again", 40.0, -74.0)
    assert reply == "ack: hello again"
    assert seen["history_len"] == 3  # 2 prior + new user msg
    assert thread_svc.get_messages(t["id"])[-1]["content"] == reply


def test_watchlist_board_and_stale():
    w = watch_svc.add_watch("org1", "atorvastatin", "20mg", 30)
    assert watch_svc.board("org1")[0]["stale"] is True  # never checked
    watch_svc.save_snapshot(w["id"], {"drug": "atorvastatin", "results": []})
    row = watch_svc.board("org1")[0]
    assert row["stale"] is False and row["snapshot"]["result"]["drug"] == "atorvastatin"
    assert watch_svc.remove_watch(w["id"], "org1")
    assert watch_svc.list_watches("org1") == []


def test_api_threads_and_board():
    from fastapi.testclient import TestClient

    from server import app

    c = TestClient(app)
    t = c.post("/api/threads", json={"org_id": "apitest"}).json()
    assert c.get(f"/api/threads/{t['id']}/messages").json() == []
    w = c.post("/api/watchlist", json={"org_id": "apitest", "drug": "lisinopril"}).json()
    board = c.get("/api/board", params={"org_id": "apitest"}).json()
    assert len(board) == 1 and board[0]["stale"] is True
    assert c.delete(f"/api/watchlist/{w['id']}", params={"org_id": "apitest"}).status_code == 200
