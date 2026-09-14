"""CALL-E adapter tests: real-API request shape, result parsing, mock mode, demo storefront."""

import services.calle as calle
import services.places as places_mod
from agent.models import Pharmacy
from services.calle import (
    _build_task,
    _parse_result_schema,
    _transcript_url,
    get_call_result,
    get_transcript_text,
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
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

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


def test_force_mock_overrides_live_key(monkeypatch):
    monkeypatch.setenv("CALLE_API_KEY", "live-key")
    monkeypatch.setenv("AGENTRX_MOCK", "1")

    def no_http(*a, **k):
        raise AssertionError("no HTTP in forced mock")

    monkeypatch.setattr(calle.requests, "post", no_http)
    monkeypatch.setattr(calle.requests, "get", no_http)
    cid = place_stock_call(_pharmacy(), "paracetamol", "", 30)
    assert cid.startswith("mock-")
    r = get_call_result(cid)
    assert r.call_status == "completed" and r.in_stock is True


def test_mock_oos_override(monkeypatch):
    monkeypatch.delenv("CALLE_API_KEY", raising=False)
    monkeypatch.delenv("CALL_E_API_KEY", raising=False)
    monkeypatch.setenv("AGENTRX_MOCK_OOS", "shortage-demo")
    cid = place_stock_call(_pharmacy(), "shortage-demo-xr", "", 30)
    assert get_call_result(cid).in_stock is False
    cid2 = place_stock_call(_pharmacy(), "paracetamol", "", 30)
    assert get_call_result(cid2).in_stock is True  # override is selective


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


def test_region_inferred_from_country_code():
    assert calle._region_for("+15551234567") == "US"
    assert calle._region_for("+2347000000000") == "NG"  # synthetic fixture, never dialed
    assert calle._region_for("+447700900123") == "GB"
    assert calle._region_for("+999unknown") == "US"


def test_place_call_real_mode_ng_region(monkeypatch):
    monkeypatch.setenv("CALLE_API_KEY", "k")
    seen = {}
    monkeypatch.setattr(
        calle.requests,
        "post",
        lambda url, headers=None, json=None, timeout=None: seen.update(json=json)
        or _FakeResp({"id": "c1"}),
    )
    place_stock_call(_pharmacy(phone="+2347000000000"), "d", "s", 1)  # synthetic fixture, never dialed
    assert seen["json"]["recipients"][0]["region"] == "NG"


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


def test_mock_transcript_dialogue(monkeypatch):
    monkeypatch.delenv("CALLE_API_KEY", raising=False)
    monkeypatch.delenv("CALL_E_API_KEY", raising=False)
    cid = place_stock_call(_pharmacy(), "paracetamol", "", 30)
    t = get_transcript_text(cid)
    assert "Agent:" in t and "Pharmacy:" in t
    assert "paracetamol" in t and "$46.39" in t


def test_mock_transcript_oos(monkeypatch):
    monkeypatch.delenv("CALLE_API_KEY", raising=False)
    monkeypatch.delenv("CALL_E_API_KEY", raising=False)
    monkeypatch.setenv("AGENTRX_MOCK_OOS", "shortage-demo")
    cid = place_stock_call(_pharmacy(), "shortage-demo", "", 30)
    t = get_transcript_text(cid)
    assert "Pharmacy:" in t and "don't have" in t


def test_transcript_unknown_call_without_key():
    assert get_transcript_text("nope-123") is None


def test_mock_pickup_varies_but_stable(monkeypatch):
    from services.calle import MOCK_PICKUPS

    monkeypatch.delenv("CALLE_API_KEY", raising=False)
    monkeypatch.delenv("CALL_E_API_KEY", raising=False)
    seen = set()
    for pid in ("mock-cvs-main", "mock-walgreens-oak", "mock-riteaid-pine"):
        cid = place_stock_call(_pharmacy(pid), "atorvastatin", "", 30)
        r = get_call_result(cid)
        if r.in_stock:
            assert r.pickup_time in MOCK_PICKUPS
            seen.add(r.pickup_time)
        # deterministic: same pharmacy+drug always agrees with itself
        cid2 = place_stock_call(_pharmacy(pid), "atorvastatin", "", 30)
        assert get_call_result(cid2).pickup_time == r.pickup_time
    assert len(seen) >= 2  # comparison spread across pharmacies
