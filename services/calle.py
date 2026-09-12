"""CALL-E adapter: place_stock_call + get_call_result.

Real mode (env CALL_E_API_KEY [+ CALL_E_BASE_URL / CALL_E_MCP_COMMAND]):
  POST {base}/calls {to, script} -> {call_id}; GET {base}/calls/{id} -> result.
Mock mode (no key): deterministic in-stock/price per pharmacy so the
comparison table + video work without spending the 20-call budget.

Call script (per spec):
  "Hi, calling to check availability and cash price for {drug} {strength}
   qty {qty}, no insurance, for pickup today?"
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid

import requests

from agent.models import CheckResult, Pharmacy

CALL_SCRIPT_TEMPLATE = (
    "Hi, calling to check availability and cash price for {drug} {strength}, "
    "quantity {qty}, no insurance, for pickup today?"
)

_mock_store: dict[str, dict] = {}


def build_script(drug: str, strength: str, qty: int) -> str:
    return CALL_SCRIPT_TEMPLATE.format(drug=drug, strength=strength, qty=qty)


def _mock_result(call_id: str) -> CheckResult:
    entry = _mock_store[call_id]
    ph: Pharmacy = entry["pharmacy"]
    # Deterministic pseudo-random per pharmacy+drug so demos are stable.
    seed = int(hashlib.md5(f"{ph.pharmacy_id}|{entry['drug']}".encode()).hexdigest()[:8], 16)
    in_stock = (seed % 4) != 0  # ~75% in stock; some OOS for realism
    price = round(8 + (seed % 4000) / 100.0, 2) if in_stock else None
    pickup = "today by 5pm" if in_stock else None
    return CheckResult(
        pharmacy=ph,
        in_stock=in_stock,
        price=price,
        pickup_time=pickup,
        transcript_url=f"mock://transcript/{call_id}",
        call_id=call_id,
        call_status="completed",
        raw_notes="mock CALL-E result (no live call placed)",
    )


def _base_url() -> str:
    return os.environ.get("CALL_E_BASE_URL", "https://api.call-e.example/v1").rstrip("/")


def place_stock_call(pharmacy: Pharmacy, drug: str, strength: str, qty: int) -> str:
    """Place one stock-check call. Returns call_id."""
    script = build_script(drug, strength, qty)
    if not os.environ.get("CALL_E_API_KEY"):
        call_id = f"mock-{uuid.uuid4().hex[:8]}"
        _mock_store[call_id] = {
            "pharmacy": pharmacy,
            "drug": drug,
            "strength": strength,
            "qty": qty,
            "script": script,
            "placed_at": time.time(),
        }
        return call_id
    resp = requests.post(
        f"{_base_url()}/calls",
        headers={"Authorization": f"Bearer {os.environ['CALL_E_API_KEY']}"},
        json={"to": pharmacy.phone, "script": script, "metadata": {"pharmacy_id": pharmacy.pharmacy_id}},
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["call_id"])


def get_call_result(call_id: str, timeout_s: int = 180, poll_s: int = 5) -> CheckResult:
    """Poll until terminal result. Mock returns immediately."""
    if call_id in _mock_store:
        return _mock_result(call_id)
    deadline = time.time() + timeout_s
    last: CheckResult | None = None
    while time.time() < deadline:
        resp = requests.get(
            f"{_base_url()}/calls/{call_id}",
            headers={"Authorization": f"Bearer {os.environ['CALL_E_API_KEY']}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status", "")).lower()
        if status in ("completed", "failed", "no-answer", "busy"):
            ph = data.get("pharmacy") or {}
            pharmacy = Pharmacy(
                pharmacy_id=str(ph.get("pharmacy_id", call_id)),
                name=str(ph.get("name", "Unknown")),
                address=str(ph.get("address", "")),
                phone=str(ph.get("phone", "")),
                rating=float(ph.get("rating", 0) or 0),
                user_ratings_total=int(ph.get("user_ratings_total", 0) or 0),
                open_now=bool(ph.get("open_now", True)),
                lat=float(ph.get("lat", 0) or 0),
                lng=float(ph.get("lng", 0) or 0),
            )
            return CheckResult(
                pharmacy=pharmacy,
                in_stock=bool(data.get("in_stock", False)),
                price=data.get("price"),
                pickup_time=data.get("pickup_time"),
                transcript_url=data.get("transcript_url"),
                call_id=call_id,
                call_status=status,
                raw_notes=str(data.get("notes", "")),
            )
        time.sleep(poll_s)
    if last is not None:
        return last
    raise TimeoutError(f"CALL-E result not ready for {call_id} after {timeout_s}s")
