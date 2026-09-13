"""CALL-E adapter: place_stock_call + get_call_result.

Real mode (env CALLE_API_KEY):
  POST {base}/v1/calls {task, recipients[{phones, region, locale}], result_schema}
    -> {call_id}; GET {base}/v1/calls/{id} -> status + structured_result.
  Docs: https://github.com/CALLE-AI/call-e-integrations
Mock mode (no key): deterministic in-stock/price per pharmacy so the
comparison table + video work without spending the call budget.

Call script (per spec):
  "Hi, calling to check availability and cash price for {drug} {strength}
   quantity {qty}, no insurance, for pickup today?"
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

RESULT_SCHEMA = {
    "type": "object",
    "required": ["in_stock"],
    "properties": {
        "in_stock": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "cash_price_usd": {"type": "number"},
        "pickup_time": {"type": "string"},
    },
}

DEFAULT_BASE_URL = "https://api.heycall-e.com"

# CALL-E region/line routing by calling code (subset of their supported list).
_REGION_BY_CC = [
    ("+1", "US"), ("+44", "GB"), ("+234", "NG"), ("+65", "SG"), ("+91", "IN"),
    ("+61", "AU"), ("+49", "DE"), ("+33", "FR"), ("+52", "MX"), ("+55", "BR"),
    ("+81", "JP"), ("+254", "KE"), ("+27", "ZA"), ("+971", "AE"), ("+60", "MY"),
]
_DEFAULT_REGION = "US"


def _region_for(phone: str) -> str:
    for cc, region in _REGION_BY_CC:
        if phone.startswith(cc):
            return region
    return _DEFAULT_REGION

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
    return os.environ.get("CALLE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str | None:
    return os.environ.get("CALLE_API_KEY") or os.environ.get("CALL_E_API_KEY")


def _build_task(drug: str, strength: str, qty: int) -> str:
    script = build_script(drug, strength, qty)
    return (
        f"Call the pharmacy. Say exactly: '{script}' Then ask whether the "
        "medication is in stock, what the cash price is in US dollars, and "
        "when it could be ready for pickup. Fill the result: in_stock "
        "('yes'/'no'/'unknown'), cash_price_usd (number, omit if unknown), "
        "pickup_time (short phrase like 'today by 5pm')."
    )


def place_stock_call(pharmacy: Pharmacy, drug: str, strength: str, qty: int) -> str:
    """Place one stock-check call. Returns call_id."""
    if not _api_key():
        script = build_script(drug, strength, qty)
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
        f"{_base_url()}/v1/calls",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Idempotency-Key": f"agentrx-{pharmacy.pharmacy_id}-{uuid.uuid4().hex[:12]}",
        },
        json={
            "task": _build_task(drug, strength, qty),
            "recipients": [
                {
                    "phones": [pharmacy.phone],
                    "region": _region_for(pharmacy.phone),
                    "locale": "en-US",
                }
            ],
            "result_schema": RESULT_SCHEMA,
            "metadata": {"pharmacy_id": pharmacy.pharmacy_id},
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    call_id = data.get("call_id") or data.get("id")
    if not call_id:
        raise ValueError(f"CALL-E create call returned no call_id: {data}")
    return str(call_id)


def _parse_result_schema(data: dict) -> tuple[bool, float | None, str | None]:
    sr = data.get("structured_result") or {}
    if not sr and data.get("recipients"):
        recips = data["recipients"] or []
        if recips:
            sr = recips[0].get("structured_result") or {}
    in_stock_raw = str(sr.get("in_stock", "unknown")).strip().lower()
    in_stock = in_stock_raw == "yes"
    price = sr.get("cash_price_usd")
    price = round(float(price), 2) if price is not None else None
    pickup = sr.get("pickup_time")
    return in_stock, price, (str(pickup) if pickup else None)


def _transcript_url(data: dict, call_id: str) -> str | None:
    turns = []
    for rec in data.get("recipients") or []:
        for attempt in rec.get("attempts") or []:
            turns.extend(attempt.get("transcript_turns") or [])
    if not turns:
        return None
    lines = [f"{t.get('speaker', '?')}: {t.get('text', '')}" for t in turns]
    return f"calle://{call_id}\n" + "\n".join(lines)


def get_call_result(call_id: str, timeout_s: int = 300, poll_s: int = 10) -> CheckResult:
    """Poll until terminal result. Mock returns immediately."""
    if call_id in _mock_store:
        return _mock_result(call_id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = requests.get(
            f"{_base_url()}/v1/calls/{call_id}",
            headers={"Authorization": f"Bearer {_api_key()}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status", "")).lower()
        if status in ("completed", "failed", "no-answer", "busy", "cancelled"):
            in_stock, price, pickup = _parse_result_schema(data)
            notes = data.get("evidence") or []
            if isinstance(notes, list):
                notes = " | ".join(str(n) for n in notes)
            return CheckResult(
                pharmacy=data.get("pharmacy") or _unknown_pharmacy(call_id),
                in_stock=in_stock,
                price=price,
                pickup_time=pickup,
                transcript_url=_transcript_url(data, call_id),
                call_id=call_id,
                call_status=status if status != "cancelled" else "failed",
                raw_notes=str(notes or ""),
            )
        time.sleep(poll_s)
    raise TimeoutError(f"CALL-E result not ready for {call_id} after {timeout_s}s")


def _unknown_pharmacy(call_id: str) -> Pharmacy:
    return Pharmacy(
        pharmacy_id=call_id,
        name="Unknown pharmacy",
        address="",
        phone="",
        rating=0.0,
        user_ratings_total=0,
        open_now=True,
        lat=0.0,
        lng=0.0,
    )
