"""Strands agent wiring (Agents-for-Humans track: Strands + AgentCore).

Model: Bedrock amazon.nova-micro (default) or anthropic.claude-3-haiku.
Falls back to a no-LLM passthrough that calls the orchestrator directly
when Bedrock creds are absent, so the demo never breaks.

OUT of scope guardrails: the system prompt forbids ordering, payment,
Rx submission, allergy/interaction consults, and dosage advice.
"""

from __future__ import annotations

import os

SYSTEM_PROMPT = """You are agentRx, a pharmacy stock-check helper.
You may ONLY: find nearby pharmacies, place stock/price check calls, summarize
results in a comparison table, and set refill reminders.
NEVER: order medication, take payment, accept Rx numbers, give allergy /
interaction / dosage advice. End every answer with exactly:
"Info only, confirm with pharmacist/doctor."
If all pharmacies are out of stock or calls fail, hand off to a human
pharmacist and say so clearly."""

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def build_agent():
    """Build the Strands agent. Raises ImportError/RuntimeError if unusable."""
    from strands import Agent, tool

    from agent.tools import (
        find_pharmacies as _find,
        get_call_result as _get,
        place_stock_call as _place,
        set_reminder as _remind,
    )

    @tool
    def find_pharmacies(lat: float, lng: float, radius_km: float = 5, delivery: bool = False) -> list[dict]:
        """Find top-3 reputable open pharmacies within radius_km. delivery=True prefers delivery options."""
        return [p.to_dict() for p in _find(lat, lng, radius_km, delivery)]

    @tool
    def place_stock_call(pharmacy_id: str, drug: str, strength: str, qty: int) -> str:
        """Place a stock/price check call. Returns call_id."""
        return _place(pharmacy_id, drug, strength, qty)

    @tool
    def get_call_result(call_id: str) -> dict:
        """Fetch a structured stock-check result for call_id."""
        return _get(call_id).to_dict()

    @tool
    def set_reminder(user_id: str, drug: str, days_supply: int, last_date: str) -> dict:
        """Set a refill reminder (remind = last_date + days_supply - 7)."""
        return _remind(user_id, drug, days_supply, last_date).to_dict()

    from strands.models import BedrockModel

    model = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[find_pharmacies, place_stock_call, get_call_result, set_reminder],
    )


def agent_available() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
