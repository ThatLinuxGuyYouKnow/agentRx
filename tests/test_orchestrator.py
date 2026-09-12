"""Orchestrator: fan-out/fan-in + handoff paths."""

from agent import tools
from agent.models import Pharmacy, SearchOutcome
from agent.orchestrator import run_search


def _pharm(i: int) -> Pharmacy:
    return Pharmacy(
        pharmacy_id=f"p{i}", name=f"P{i}", address="a", phone=f"+1555000{i:04d}",
        rating=4.5, user_ratings_total=100, open_now=True,
        lat=40.0, lng=-74.0, distance_km=float(i),
    )


def test_happy_path_sorted(monkeypatch):
    monkeypatch.setattr(tools, "find_pharmacies", lambda *a, **k: [_pharm(1), _pharm(2), _pharm(3)])
    monkeypatch.setattr(tools, "place_stock_call", lambda pid, *a: f"call-{pid}")
    from agent.models import CheckResult

    def fake_result(cid):
        i = int(cid[-1])
        return CheckResult(
            pharmacy=_pharm(i), in_stock=(i != 3), price={1: 12.5, 2: 9.99}.get(i),
            pickup_time="today", transcript_url=f"t/{cid}", call_id=cid,
        )

    monkeypatch.setattr(tools, "get_call_result", fake_result)
    out = run_search("atorvastatin", "20mg", 30, 40.0, -74.0)
    assert isinstance(out, SearchOutcome)
    assert not out.needs_human
    assert [r.pharmacy.pharmacy_id for r in out.results][:2] == ["p2", "p1"]  # cheapest first


def test_all_oos_handoff(monkeypatch):
    monkeypatch.setattr(tools, "find_pharmacies", lambda *a, **k: [_pharm(1)])
    monkeypatch.setattr(tools, "place_stock_call", lambda pid, *a: "c1")
    from agent.models import CheckResult

    monkeypatch.setattr(
        tools, "get_call_result",
        lambda cid: CheckResult(pharmacy=_pharm(1), in_stock=False, price=None,
                               pickup_time=None, transcript_url="t", call_id=cid),
    )
    out = run_search("atorvastatin", "20mg", 30, 40.0, -74.0)
    assert out.needs_human and "out of stock" in out.handoff_reason


def test_no_pharmacies_handoff(monkeypatch):
    monkeypatch.setattr(tools, "find_pharmacies", lambda *a, **k: [])
    out = run_search("atorvastatin", "20mg", 30, 40.0, -74.0)
    assert out.needs_human
