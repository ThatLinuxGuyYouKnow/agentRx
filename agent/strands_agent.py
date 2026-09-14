"""Strands agent wiring (Agents-for-Humans track: Strands + AgentCore).

Good Neighbor graph in :mod:`agent.coordinator`: a supervisor with
finder / caller / board sub-agents (via ``Agent.as_tool()``) over shared
domain tools.

Model: Bedrock amazon.nova-micro (default) or anthropic.claude-3-haiku.
Without Bedrock creds the same graph runs on the deterministic offline
model through the real Strands event loop, so the demo never breaks.

OUT of scope guardrails: the system prompt forbids ordering, payment,
Rx submission, allergy/interaction consults, and dosage advice.
"""

from __future__ import annotations

import os

SYSTEM_PROMPT = """You are agentRx, the Good Neighbor refill coordinator for a
community org (senior center / clinic / mutual-aid group). You may ONLY:
find nearby pharmacies, propose call plans, summarize the shortage board,
and set refill reminders.
NEVER: order medication, take payment, accept Rx numbers, give allergy /
interaction / dosage advice. End every answer with exactly:
"Info only, confirm with pharmacist/doctor."
If all pharmacies are out of stock or calls fail, hand off to a human
pharmacist and say so clearly."""

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def build_agent(org_id: str = "demo-org", lat: float = 40.7128, lng: float = -74.0060,
                history: list | None = None):
    """Build the supervisor Agent of the Good Neighbor graph."""
    from agent import coordinator as coord

    return coord.build_graph(org_id, lat, lng, history)["coordinator"]


def build_graph(org_id: str = "demo-org", lat: float = 40.7128,
                lng: float = -74.0060, history: list | None = None):
    """Full multi-agent graph (finder / caller / board / coordinator)."""
    from agent import coordinator as coord

    return coord.build_graph(org_id, lat, lng, history)


def agent_available() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
