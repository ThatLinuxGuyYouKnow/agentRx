"""API tests (FastAPI TestClient, mock backends)."""

import os

os.environ.setdefault("AGENTRX_DB_PATH", "/tmp/opencode/agentrx_api_test.db")

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402

client = TestClient(app)


def test_search_returns_table():
    r = client.post(
        "/api/search",
        json={"drug": "atorvastatin", "strength": "20mg", "qty": 30, "lat": 40.7, "lng": -74.0},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 3
    assert "disclaimer" in data
    first = data["results"][0]
    assert {"pharmacy", "in_stock", "price", "pickup_time", "transcript_url", "call_id"} <= set(first)


def test_reminder_crud():
    r = client.post(
        "/api/reminders",
        json={"user_id": "uitest", "drug": "atorvastatin", "days_supply": 30, "last_date": "2026-09-04"},
    )
    assert r.status_code == 200
    rid = r.json()["id"]
    assert r.json()["remind_date"] == "2026-09-27"
    listed = client.get("/api/reminders", params={"user_id": "uitest"}).json()
    assert any(x["id"] == rid for x in listed)
    assert client.delete(f"/api/reminders/{rid}").status_code == 200


def test_index_serves():
    r = client.get("/")
    assert r.status_code == 200 and "AgentRx" in r.text


def test_consent_flow_pharmacies_then_check():
    cands = client.post("/api/pharmacies", json={"lat": 40.7, "lng": -74.0, "radius_km": 5}).json()
    assert len(cands["pharmacies"]) == 3
    # user deselects down to one
    approved = cands["pharmacies"][:1]
    out = client.post(
        "/api/check",
        json={"drug": "atorvastatin", "strength": "20mg", "qty": 30,
              "lat": 40.7, "lng": -74.0, "pharmacies": approved},
    ).json()
    assert len(out["results"]) == 1
    assert out["results"][0]["pharmacy"]["pharmacy_id"] == approved[0]["pharmacy_id"]


def test_check_rejects_empty_selection():
    out = client.post(
        "/api/check",
        json={"drug": "atorvastatin", "strength": "20mg", "qty": 30, "pharmacies": []},
    ).json()
    assert out["needs_human"] and out["handoff_reason"] == "no pharmacies selected"


def test_email_summary_unconfigured():
    r = client.post(
        "/api/email-summary",
        json={"to_email": "x@y.z", "drug": "atorvastatin", "summary": "- a: in stock"},
    )
    assert r.status_code == 501
