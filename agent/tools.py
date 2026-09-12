"""Strands tool contracts (exact signatures from spec).

- find_pharmacies(lat, lng, radius_km=5) -> Pharmacy[3]
- place_stock_call(pharmacy_id, drug, strength, qty) -> call_id
- get_call_result(call_id) -> CheckResult
- set_reminder(user_id, drug, days_supply, last_date)

The orchestrator calls these directly; the Strands agent exposes the same
functions as @tool so the LLM path uses identical behavior.
"""

from __future__ import annotations

from agent.models import CheckResult, Pharmacy
from services import calle, places
from services import reminders as reminder_svc

# Resolve pharmacy_id -> Pharmacy for the current search (set by orchestrator).
_pharmacy_index: dict[str, Pharmacy] = {}


def set_pharmacy_index(pharmacies: list[Pharmacy]) -> None:
    _pharmacy_index.clear()
    for p in pharmacies:
        _pharmacy_index[p.pharmacy_id] = p


def find_pharmacies(
    lat: float, lng: float, radius_km: float = 5, delivery: bool = False
) -> list[Pharmacy]:
    pharmacies = places.find_pharmacies(lat, lng, radius_km, delivery)
    set_pharmacy_index(pharmacies)
    return pharmacies


def place_stock_call(pharmacy_id: str, drug: str, strength: str, qty: int) -> str:
    pharmacy = _pharmacy_index.get(pharmacy_id)
    if pharmacy is None:
        raise ValueError(f"unknown pharmacy_id: {pharmacy_id}")
    return calle.place_stock_call(pharmacy, drug, strength, qty)


def get_call_result(call_id: str) -> CheckResult:
    return calle.get_call_result(call_id)


def set_reminder(user_id: str, drug: str, days_supply: int, last_date: str):
    return reminder_svc.set_reminder(user_id, drug, days_supply, last_date)
