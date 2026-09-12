"""Filter + mock pharmacy tests."""

import os

import services.places as places_mod
from services.places import MIN_RATINGS_TOTAL, MIN_RATING, _parse_new_place, _passes_filter, find_pharmacies


def _new_place(pid, rating, count, open_now, phone="+15551234567", dlat=0.005):
    return {
        "id": pid,
        "displayName": {"text": f"Pharm {pid}"},
        "formattedAddress": "1 Main St",
        "rating": rating,
        "userRatingCount": count,
        "currentOpeningHours": {"openNow": open_now},
        "location": {"latitude": 40.0 + dlat, "longitude": -74.0},
        "nationalPhoneNumber": phone,
    }


def test_filter_thresholds():
    assert _passes_filter(4.0, 51, True)
    assert not _passes_filter(3.9, 500, True)
    assert not _passes_filter(4.5, 50, True)
    assert not _passes_filter(4.5, 500, False)


def test_mock_top3_shape(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    res = find_pharmacies(40.7128, -74.0060)
    assert len(res) == 3
    for p in res:
        assert p.rating >= MIN_RATING
        assert p.user_ratings_total > MIN_RATINGS_TOTAL
        assert p.open_now
        assert p.phone


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _run_live(monkeypatch, places):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(places_mod.requests, "post", lambda *a, **k: _FakeResp({"places": places}))
    return find_pharmacies(40.0, -74.0)


def test_live_strict_top3(monkeypatch):
    places = [
        _new_place("a", 4.6, 300, True),
        _new_place("b", 4.2, 120, True),
        _new_place("c", 4.8, 500, True),
        _new_place("d", 3.9, 400, True),  # strict-fail, relaxed-ok
    ]
    res = _run_live(monkeypatch, places)
    assert [p.pharmacy_id for p in res] == ["c", "a", "b"]  # rating desc


def test_live_relaxed_fills_shortfall(monkeypatch):
    places = [
        _new_place("a", 4.6, 300, True),
        _new_place("b", 3.7, 60, True),   # relaxed
        _new_place("c", 3.6, 40, False),  # relaxed, closed
        _new_place("d", 2.0, 5, True),    # junk
    ]
    res = _run_live(monkeypatch, places)
    assert [p.pharmacy_id for p in res] == ["a", "b", "c"]


def test_live_empty_when_nothing_reputable(monkeypatch):
    assert _run_live(monkeypatch, [_new_place("d", 2.0, 5, True)]) == []


def test_live_prefers_results_with_phone(monkeypatch):
    places = [
        _new_place("nophone1", 4.9, 900, True, phone=""),
        _new_place("nophone2", 4.8, 800, True, phone=""),
        _new_place("nophone3", 4.7, 700, True, phone=""),
        _new_place("ok1", 4.1, 100, True),
        _new_place("ok2", 4.0, 90, True),
        _new_place("ok3", 4.0, 80, True),
    ]
    res = _run_live(monkeypatch, places)
    assert all(p.phone for p in res) and len(res) == 3


def test_parse_skips_malformed():
    assert _parse_new_place({"id": "x"}, 40.0, -74.0) is None
    p = _parse_new_place(_new_place("a", 4.5, 200, True), 40.0, -74.0)
    assert p and p.name == "Pharm a" and p.phone.startswith("+1")


def test_delivery_search_uses_text_endpoint_and_flags(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return _FakeResp({"places": [_new_place("d1", 4.4, 120, True)]})

    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(places_mod.requests, "post", fake_post)
    res = find_pharmacies(40.0, -74.0, radius_km=2, delivery=True)
    assert seen["url"].endswith("places:searchText")
    assert len(res) == 1 and res[0].delivery is True
