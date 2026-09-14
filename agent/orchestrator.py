"""Orchestrator: Finder -> (consent gate) -> Caller (fan-out) -> Aggregator.

Two-step flow so the user sees candidates and approves before any call is
placed: find_candidates() then check_stock() on the user-selected subset.
run_search() keeps the old one-shot behavior.

Handoff to human if all out-of-stock or all calls fail.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent import tools
from agent.models import Pharmacy, SearchOutcome

logger = logging.getLogger("agentRx.orchestrator")

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
    if not drug:
        return SearchOutcome(results=[], needs_human=True, handoff_reason="missing drug name")
    if qty <= 0:
        return SearchOutcome(results=[], needs_human=True, handoff_reason="invalid quantity")
    if not pharmacies:
        return SearchOutcome(
            results=[], needs_human=True, handoff_reason="no pharmacies selected"
        )
    tools.set_pharmacy_index(pharmacies)
    logger.info(
        "check_stock start: drug=%r strength=%r qty=%s pharmacies=%s",
        drug, strength, qty, [p.pharmacy_id for p in pharmacies],
    )

    # Caller (fan-out, parallel)
    call_ids: dict[str, str] = {}  # pharmacy_id -> call_id
    place_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {
            pool.submit(tools.place_stock_call, p.pharmacy_id, drug, strength, qty): p
            for p in pharmacies
        }
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                call_ids[p.pharmacy_id] = fut.result()
            except Exception as e:
                err = f"{p.pharmacy_id} ({p.phone}): {e}"
                place_errors.append(err)
                logger.warning("place_stock_call failed: %s", err, exc_info=True)

    if not call_ids:
        detail = f": {place_errors[0]}" if place_errors else ""
        reason = f"all calls failed to place{detail}"[:300]
        logger.error(
            "check_stock handoff: %s (failures=%d)", reason, len(place_errors)
        )
        return SearchOutcome(results=[], needs_human=True, handoff_reason=reason)
    if place_errors:
        logger.warning(
            "check_stock partial placement: %d/%d placed (%s)",
            len(call_ids), len(pharmacies), "; ".join(place_errors)[:300],
        )

    # Aggregator (fan-in, parallel result fetch)
    results = []
    result_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(tools.get_call_result, cid): cid for cid in call_ids.values()}
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                err = f"{cid}: {e}"
                result_errors.append(err)
                logger.warning("get_call_result failed: %s", err, exc_info=True)

    if not results:
        detail = f": {result_errors[0]}" if result_errors else ""
        reason = f"all calls failed{detail}"[:300]
        logger.error("check_stock handoff: %s", reason)
        return SearchOutcome(results=[], needs_human=True, handoff_reason=reason)
    if all(not r.in_stock for r in results):
        logger.warning(
            "check_stock handoff: all %d pharmacies out of stock (drug=%r)",
            len(results), drug,
        )
        return SearchOutcome(
            results=sorted(results, key=lambda r: (r.price is None, r.price or 0)),
            needs_human=True,
            handoff_reason="all pharmacies out of stock",
        )
    # Cheapest in-stock first; unknown-price last.
    results.sort(key=lambda r: (not r.in_stock, r.price is None, r.price or 0))
    logger.info(
        "check_stock done: %d results, %d in stock (drug=%r)",
        len(results), sum(1 for r in results if r.in_stock), drug,
    )
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
