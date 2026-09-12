"""Orchestrator: Finder -> (consent gate) -> Caller (fan-out) -> Aggregator.

Two-step flow so the user sees candidates and approves before any call is
placed: find_candidates() then check_stock() on the user-selected subset.
run_search() keeps the old one-shot behavior.

Handoff to human if all out-of-stock or all calls fail.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from agent import tools
from agent.models import Pharmacy, SearchOutcome

DISCLAIMER = "Info only, confirm with pharmacist/doctor."

MAX_WORKERS = 3


def find_candidates(
    lat: float, lng: float, radius_km: float = 5, delivery: bool = False
) -> list[Pharmacy]:
    """Finder only: no calls placed. Returns candidates for user approval."""
    return tools.find_pharmacies(lat, lng, radius_km, delivery)


def check_stock(
    drug: str, strength: str, qty: int, pharmacies: list[Pharmacy]
) -> SearchOutcome:
    """Caller (fan-out) + Aggregator over the user-approved subset."""
    drug, strength = drug.strip(), strength.strip()
    if not drug or not strength or qty <= 0:
        return SearchOutcome(results=[], needs_human=True, handoff_reason="invalid input")
    if not pharmacies:
        return SearchOutcome(
            results=[], needs_human=True, handoff_reason="no pharmacies selected"
        )
    tools.set_pharmacy_index(pharmacies)

    # Caller (fan-out, parallel)
    call_ids: dict[str, str] = {}  # pharmacy_id -> call_id
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {
            pool.submit(tools.place_stock_call, p.pharmacy_id, drug, strength, qty): p
            for p in pharmacies
        }
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                call_ids[p.pharmacy_id] = fut.result()
            except Exception:
                continue

    if not call_ids:
        return SearchOutcome(results=[], needs_human=True, handoff_reason="all calls failed to place")

    # Aggregator (fan-in, parallel result fetch)
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(tools.get_call_result, cid): cid for cid in call_ids.values()}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception:
                continue

    if not results:
        return SearchOutcome(results=[], needs_human=True, handoff_reason="all calls failed")
    if all(not r.in_stock for r in results):
        return SearchOutcome(
            results=sorted(results, key=lambda r: (r.price is None, r.price or 0)),
            needs_human=True,
            handoff_reason="all pharmacies out of stock",
        )
    # Cheapest in-stock first; unknown-price last.
    results.sort(key=lambda r: (not r.in_stock, r.price is None, r.price or 0))
    return SearchOutcome(results=results)


def run_search(
    drug: str, strength: str, qty: int, lat: float, lng: float, radius_km: float = 5
) -> SearchOutcome:
    """One-shot Finder -> Caller -> Aggregator (no consent gate)."""
    pharmacies = find_candidates(lat, lng, radius_km)
    if not pharmacies:
        return SearchOutcome(
            results=[], needs_human=True,
            handoff_reason="no reputable open pharmacies found nearby",
        )
    return check_stock(drug, strength, qty, pharmacies)
