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
import logging
import os
import time
import uuid

import requests

from agent.models import CheckResult, Pharmacy

logger = logging.getLogger("agentRx.calle")

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

# Demo comparison spread: deterministic per pharmacy+drug so takes are stable,
# varied enough that price x distance x pickup trade-offs show in the table.
MOCK_PICKUPS = ["today by 5pm", "tomorrow 9am", "within the hour", "today by 8pm"]


def build_script(drug: str, strength: str, qty: int) -> str:
    label = f"{(drug or '').strip()} {(strength or '').strip()}".strip()
    return (
        f"Hi, calling to check availability and cash price for {label}, "
        f"quantity {qty}, no insurance, for pickup today?"
    )


def _mock_result(call_id: str) -> CheckResult:
    entry = _mock_store[call_id]
    ph: Pharmacy = entry["pharmacy"]
    if mock_oos_for(entry["drug"]):
        logger.info(
            "CALL-E mock forced OOS (AGENTRX_MOCK_OOS): drug=%r pharmacy_id=%s",
            entry["drug"], ph.pharmacy_id,
        )
        return CheckResult(
            pharmacy=ph,
            in_stock=False,
            price=None,
            pickup_time=None,
            transcript_url=f"mock://transcript/{call_id}",
            call_id=call_id,
            call_status="completed",
            raw_notes="mock CALL-E result (forced out-of-stock demo)",
        )
    # Deterministic pseudo-random per pharmacy+drug so demos are stable.
    seed = int(hashlib.md5(f"{ph.pharmacy_id}|{entry['drug']}".encode()).hexdigest()[:8], 16)
    in_stock = (seed % 4) != 0  # ~75% in stock; some OOS for realism
    price = round(8 + (seed % 4000) / 100.0, 2) if in_stock else None
    pickup = MOCK_PICKUPS[seed % len(MOCK_PICKUPS)] if in_stock else None
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


def force_mock() -> bool:
    """Demo override: AGENTRX_MOCK=1 forces mock calls even with live keys set."""
    return os.environ.get("AGENTRX_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


def _api_key() -> str | None:
    if force_mock():
        return None
    return os.environ.get("CALLE_API_KEY") or os.environ.get("CALL_E_API_KEY")


def mock_oos_for(drug: str) -> bool:
    """Demo override: AGENTRX_MOCK_OOS='drugA,drugB' forces those mock results
    out-of-stock (substring match) so the handoff path is demoable on demand."""
    needles = [
        n.strip().lower()
        for n in os.environ.get("AGENTRX_MOCK_OOS", "").split(",")
        if n.strip()
    ]
    hay = (drug or "").lower()
    return any(n in hay for n in needles)


def _mode() -> str:
    return "live" if _api_key() else "mock"


def _body_snippet(resp: requests.Response, limit: int = 500) -> str:
    try:
        text = resp.text
    except Exception:
        return "<unreadable body>"
    return text[:limit]


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
    label = f"{(drug or '').strip()} {(strength or '').strip()}".strip()
    if not _api_key():
        logger.info(
            "CALL-E mock place call%s: pharmacy_id=%s drug=%r qty=%s",
            " (forced by AGENTRX_MOCK)" if force_mock() else " (no API key)",
            pharmacy.pharmacy_id, label, qty,
        )
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
        logger.info("CALL-E mock call placed: call_id=%s", call_id)
        return call_id
    url = f"{_base_url()}/v1/calls"
    region = _region_for(pharmacy.phone)
    payload = {
        "task": _build_task(drug, strength, qty),
        "recipients": [
            {
                "phones": [pharmacy.phone],
                "region": region,
                "locale": "en-US",
            }
        ],
        "result_schema": RESULT_SCHEMA,
        "metadata": {"pharmacy_id": pharmacy.pharmacy_id},
    }
    # NOTE: the API key is sent here but never logged (only its presence).
    logger.info(
        "CALL-E live POST %s: pharmacy_id=%s phone=%s region=%s drug=%r qty=%s",
        url, pharmacy.pharmacy_id, pharmacy.phone, region, label, qty,
    )
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Idempotency-Key": f"agentrx-{pharmacy.pharmacy_id}-{uuid.uuid4().hex[:12]}",
            },
            json=payload,
            timeout=30,
        )
    except requests.RequestException:
        logger.exception(
            "CALL-E live POST failed (no response): pharmacy_id=%s phone=%s",
            pharmacy.pharmacy_id, pharmacy.phone,
        )
        raise
    if resp.status_code in (401, 403):
        logger.error(
            "CALL-E live POST %s: pharmacy_id=%s (auth rejected)",
            resp.status_code, pharmacy.pharmacy_id,
        )
    elif resp.status_code >= 400:
        logger.error(
            "CALL-E live POST %s: pharmacy_id=%s phone=%s body=%s",
            resp.status_code, pharmacy.pharmacy_id, pharmacy.phone,
            _body_snippet(resp),
        )
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        logger.exception(
            "CALL-E live POST raise_for_status: pharmacy_id=%s status=%s",
            pharmacy.pharmacy_id, resp.status_code,
        )
        raise
    data = resp.json()
    call_id = data.get("call_id") or data.get("id")
    if not call_id:
        logger.error("CALL-E create call returned no call_id: %s", str(data)[:500])
        raise ValueError(f"CALL-E create call returned no call_id: {data}")
    logger.info(
        "CALL-E live call placed: pharmacy_id=%s call_id=%s",
        pharmacy.pharmacy_id, call_id,
    )
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


def _mock_transcript_text(entry: dict, res: CheckResult) -> str:
    """Demo dialogue for the in-app transcript viewer (Agent: / Pharmacy:)."""
    label = f"{entry.get('drug', '')} {entry.get('strength', '')}".strip()
    lines = [f"Agent: {entry.get('script', '')}"]
    if res.in_stock:
        lines.append(f"Pharmacy: Yes, we have {label} in stock.")
        lines.append("Agent: And the cash price, no insurance?")
        lines.append(
            f"Pharmacy: ${res.price:.2f}." if res.price is not None
            else "Pharmacy: I'd have to check at the counter — no price by phone."
        )
        lines.append("Agent: When could it be ready for pickup?")
        lines.append(
            f"Pharmacy: {res.pickup_time}." if res.pickup_time
            else "Pharmacy: Probably later today — call back to confirm."
        )
    else:
        lines.append(f"Pharmacy: Sorry, we don't have {label} in stock right now.")
        lines.append("Agent: Thanks — we'll check elsewhere. Goodbye!")
    return "\n".join(lines)


def get_transcript_text(call_id: str) -> str | None:
    """Best-effort transcript text for UI display.

    Mock: synthesized Agent:/Pharmacy: dialogue (instant). Live: single fetch
    of recorded turns (no polling); None when unavailable.
    """
    if call_id in _mock_store:
        entry = _mock_store[call_id]
        return _mock_transcript_text(entry, _mock_result(call_id))
    if not _api_key():
        return None
    try:
        resp = requests.get(
            f"{_base_url()}/v1/calls/{call_id}",
            headers={"Authorization": f"Bearer {_api_key()}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("transcript fetch failed: call_id=%s", call_id, exc_info=True)
        return None
    turns = []
    for rec in data.get("recipients") or []:
        for attempt in rec.get("attempts") or []:
            turns.extend(attempt.get("transcript_turns") or [])
    if not turns:
        return None
    out = []
    for t in turns:
        who = "Agent" if str(t.get("speaker", "")).lower() == "bot" else "Pharmacy"
        out.append(f"{who}: {t.get('text', '')}")
    return "\n".join(out)


def get_call_result(call_id: str, timeout_s: int = 300, poll_s: int = 10) -> CheckResult:
    """Poll until terminal result. Mock returns immediately."""
    if call_id in _mock_store:
        logger.info("CALL-E mock result fetch: call_id=%s", call_id)
        return _mock_result(call_id)
    url = f"{_base_url()}/v1/calls/{call_id}"
    logger.info("CALL-E live poll start: call_id=%s timeout_s=%s", call_id, timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {_api_key()}"},
                timeout=30,
            )
        except requests.RequestException:
            logger.exception("CALL-E live poll failed (no response): call_id=%s", call_id)
            raise
        if resp.status_code >= 400:
            logger.error(
                "CALL-E live poll %s: call_id=%s body=%s",
                resp.status_code, call_id, _body_snippet(resp),
            )
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            logger.exception(
                "CALL-E live poll raise_for_status: call_id=%s status=%s",
                call_id, resp.status_code,
            )
            raise
        data = resp.json()
        status = str(data.get("status", "")).lower()
        logger.debug("CALL-E live poll status: call_id=%s status=%r", call_id, status)
        if status in ("completed", "failed", "no-answer", "busy", "cancelled"):
            in_stock, price, pickup = _parse_result_schema(data)
            notes = data.get("evidence") or []
            if isinstance(notes, list):
                notes = " | ".join(str(n) for n in notes)
            logger.info(
                "CALL-E live result: call_id=%s status=%s in_stock=%s price=%s pickup=%r",
                call_id, status, in_stock, price, pickup,
            )
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
    logger.error("CALL-E live poll timeout: call_id=%s after %ss", call_id, timeout_s)
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
