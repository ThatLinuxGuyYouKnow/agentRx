"""Threaded neighbor agent: long-running back-and-forths with memory.

Each turn rebuilds the Strands agent with persisted thread history.
Deliberately NOT given call placement: the agent may search, watch, remind,
and summarize — placing CALL-E calls stays behind the UI consent gate
(it surfaces a call plan; the human approves).
"""

from __future__ import annotations

import os

ASSISTANT_PROMPT = """You are agentRx, the refill coordinator for a community org
(senior center / clinic / mutual-aid group). You hold an ongoing conversation
across days: remember watched drugs, past stock results, and pending approvals.

You may: search nearby pharmacies (with radius/delivery), add/remove watched
drugs, summarize the shortage board, set refill reminders.
You may NEVER place phone calls yourself. When checks are needed, propose a
call plan (which pharmacies, which drug) and ask the coordinator to approve
it in the app. End substantive answers with exactly:
"Info only, confirm with pharmacist/doctor."
Keep replies short and practical."""

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _text_of(result) -> str:
    import re

    parts = []
    try:
        for block in (result.message.get("content") or []):
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
    except (AttributeError, TypeError):
        pass
    text = "".join(parts) or str(result)
    return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()


def build_thread_agent(org_id: str, lat: float, lng: float, history: list[dict]):
    from strands import Agent, tool

    from agent import tools as base_tools
    from services import reminders as reminder_svc
    from services import watchlist as watch_svc

    @tool
    def find_pharmacies(radius_km: float = 5, delivery: bool = False) -> list[dict]:
        """Search reputable pharmacies near the user's location."""
        return [p.to_dict() for p in base_tools.find_pharmacies(lat, lng, radius_km, delivery)]

    @tool
    def watch_drug(drug: str, strength: str = "", qty: int = 30,
                   radius_km: float = 5, delivery: bool = False) -> dict:
        """Add a drug to the org's watchlist for ongoing monitoring."""
        return watch_svc.add_watch(org_id, drug, strength, qty, radius_km, delivery)

    @tool
    def unwatch_drug(drug: str) -> str:
        """Remove a drug from the watchlist by name."""
        watches = watch_svc.list_watches(org_id)
        for w in watches:
            if w["drug"].lower() == drug.strip().lower():
                watch_svc.remove_watch(w["id"], org_id)
                return f"stopped watching {w['drug']}"
        return f"{drug} was not on the watchlist"

    @tool
    def list_watchlist() -> list[dict]:
        """List watched drugs."""
        return watch_svc.list_watches(org_id)

    @tool
    def board_summary() -> list[dict]:
        """Latest board state: watches + last snapshots + stale flags."""
        return watch_svc.board(org_id)

    @tool
    def set_reminder(user_id: str, drug: str, days_supply: int, last_date: str) -> dict:
        """Set a refill reminder (remind = last_date + days_supply - 7)."""
        return reminder_svc.set_reminder(user_id, drug, days_supply, last_date).to_dict()

    from strands.models import BedrockModel

    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION),
        messages=list(history),
        system_prompt=ASSISTANT_PROMPT,
        tools=[find_pharmacies, watch_drug, unwatch_drug, list_watchlist,
               board_summary, set_reminder],
    )


def run_turn(thread_id: int, org_id: str, text: str, lat: float, lng: float) -> str:
    from services import threads as thread_svc

    thread_svc.add_message(thread_id, "user", text)
    agent = build_thread_agent(org_id, lat, lng, thread_svc.strands_history(thread_id))
    result = agent(text)
    reply = _text_of(result)
    thread_svc.add_message(thread_id, "assistant", reply)
    return reply
