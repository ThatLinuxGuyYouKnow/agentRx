"""Orchestrator: fan-out/fan-in + handoff paths."""

import logging

from agent import tools
from agent.models import Pharmacy, SearchOutcome
from agent.orchestrator import check_stock, run_search


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


def test_place_failures_logged_and_surfaced(monkeypatch, caplog):
    def boom(pid, *a):
        raise RuntimeError("400 Bad Request (bad phone)")
    monkeypatch.setattr(tools, "place_stock_call", boom)
    with caplog.at_level(logging.WARNING, logger="agentRx.orchestrator"):
        out = check_stock("paracetamol", "", 30, [_pharm(1)])
    assert out.needs_human
    assert out.handoff_reason.startswith("all calls failed to place")
    assert "400 Bad Request" in out.handoff_reason  # cause visible in UI/API
    assert any("place_stock_call failed" in r.message for r in caplog.records)


def test_result_failures_logged_and_surfaced(monkeypatch, caplog):
    monkeypatch.setattr(tools, "place_stock_call", lambda pid, *a: "c1")
    def boom(cid):
        raise TimeoutError("CALL-E result not ready")
    monkeypatch.setattr(tools, "get_call_result", boom)
    with caplog.at_level(logging.WARNING, logger="agentRx.orchestrator"):
        out = check_stock("paracetamol", "", 30, [_pharm(1)])
    assert out.needs_human
    assert out.handoff_reason.startswith("all calls failed")
    assert "not ready" in out.handoff_reason
    assert any("get_call_result failed" in r.message for r in caplog.records)
