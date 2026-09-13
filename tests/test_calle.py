"""CALL-E adapter tests: real-API request shape, result parsing, mock mode, demo storefront."""

import services.calle as calle
import services.places as places_mod
from agent.models import Pharmacy
from services.calle import (
    _build_task,
    _parse_result_schema,
    _transcript_url,
    get_call_result,
    place_stock_call,
)
from services.places import find_pharmacies


def _pharmacy(pid="demo-storefront", phone="+15551234567"):
    return Pharmacy(
        pharmacy_id=pid,
        name="AgentRx Demo Pharmacy",
        address="1 Demo St",
        phone=phone,
        rating=4.8,
        user_ratings_total=99,
        open_now=True,
        lat=40.0,
        lng=-74.0,
    )


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_build_task_contains_script():
    task = _build_task("amoxicillin", "500mg", 20)
    assert "amoxicillin 500mg" in task
    assert "quantity 20" in task
    assert "in_stock" in task


def test_place_call_real_mode_posts_v1_calls(monkeypatch):
    monkeypatch.setenv("CALLE_API_KEY", "iams_live_test")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        seen["auth"] = headers["Authorization"]
        return _FakeResp({"id": "call_123", "status": "queued"})

    monkeypatch.setattr(calle.requests, "post", fake_post)
    cid = place_stock_call(_pharmacy(), "amoxicillin", "500mg", 20)
    assert cid == "call_123"
    assert seen["url"].endswith("/v1/calls")
    assert seen["auth"] == "Bearer iams_live_test"
    assert seen["json"]["recipients"] == [
        {"phones": ["+15551234567"], "region": "US", "locale": "en-US"}
    ]
    assert seen["json"]["result_schema"]["required"] == ["in_stock"]


def test_place_call_real_mode_accepts_call_id_key(monkeypatch):
    monkeypatch.setenv("CALLE_API_KEY", "k")
    monkeypatch.setattr(
        calle.requests, "post", lambda *a, **k: _FakeResp({"call_id": "c9"})
    )
    assert place_stock_call(_pharmacy(), "d", "s", 1) == "c9"


def test_place_call_mock_mode_no_key(monkeypatch):
    monkeypatch.delenv("CALLE_API_KEY", raising=False)
    monkeypatch.delenv("CALL_E_API_KEY", raising=False)
    cid = place_stock_call(_pharmacy(), "d", "s", 1)
    assert cid.startswith("mock-")
    r = get_call_result(cid)
    assert r.call_status == "completed"


def test_parse_result_schema_structured():
    in_stock, price, pickup = _parse_result_schema(
        {"structured_result": {"in_stock": "yes", "cash_price_usd": 12.456, "pickup_time": "today 4pm"}}
    )
    assert in_stock and price == 12.46 and pickup == "today 4pm"


def test_parse_result_schema_recipient_fallback():
    in_stock, price, pickup = _parse_result_schema(
        {"recipients": [{"structured_result": {"in_stock": "no"}}]}
    )
    assert not in_stock and price is None and pickup is None


def test_transcript_url_joins_turns():
    url = _transcript_url(
        {
            "recipients": [
                {
                    "attempts": [
                        {
                            "transcript_turns": [
                                {"speaker": "bot", "text": "Hi, checking stock?"},
                                {"speaker": "user", "text": "Yes we have it."},
                            ]
                        }
                    ]
                }
            ]
        },
        "call_1",
    )
    assert url.startswith("calle://call_1")
    assert "bot: Hi, checking stock?" in url and "user: Yes we have it." in url
    assert _transcript_url({}, "call_1") is None


def test_get_call_result_completed(monkeypatch):
    monkeypatch.setenv("CALLE_API_KEY", "k")
    payload = {
        "status": "completed",
        "task_completed": True,
        "structured_result": {"in_stock": "yes", "cash_price_usd": 9.5, "pickup_time": "today by 5pm"},
        "recipients": [{"attempts": [{"transcript_turns": [{"speaker": "bot", "text": "hi"}]}]}],
        "evidence": ["Staff confirmed stock."],
    }

    class _NoSleep:
        def __call__(self, *_a):
            pass

    monkeypatch.setattr(calle.requests, "get", lambda *a, **k: _FakeResp(payload))
    monkeypatch.setattr(calle.time, "sleep", _NoSleep())
    r = get_call_result("call_1")
    assert r.in_stock and r.price == 9.5 and r.pickup_time == "today by 5pm"
    assert r.call_status == "completed"
    assert r.pharmacy.name == "Unknown pharmacy"
    assert "Staff confirmed stock." in r.raw_notes


def test_demo_storefront_injected_first_live(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)  # mock path is enough
    monkeypatch.setenv("DEMO_STOREFRONT_PHONE", "+15559990000")
    monkeypatch.setenv("DEMO_STOREFRONT_NAME", "AgentRx Demo Pharmacy")
    res = find_pharmacies(40.7128, -74.0060)
    assert res[0].pharmacy_id == "demo-storefront"
    assert res[0].phone == "+15559990000"
    assert len(res) == 3  # demo + 2 real/mock candidates


def test_demo_storefront_absent_without_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_STOREFRONT_PHONE", raising=False)
    res = find_pharmacies(40.7128, -74.0060)
    assert all(p.pharmacy_id != "demo-storefront" for p in res)
