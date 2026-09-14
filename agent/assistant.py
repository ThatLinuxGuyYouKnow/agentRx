"""Threaded Good Neighbor agent: long-running back-and-forths with memory.

Every turn runs through the Strands multi-agent graph in
:mod:`agent.coordinator` (finder / caller / board sub-agents under a
supervisor), with SQLite-backed thread history as the session memory.
Deliberately NOT given call placement: the agent may search, watch,
remind, and summarize — placing CALL-E calls stays behind the UI consent
gate (it surfaces a call plan; the human approves).
"""

from __future__ import annotations


def _text_of(result) -> str:
    from agent.coordinator import _text_of as _t

    return _t(result)


def build_thread_agent(org_id: str, lat: float, lng: float, history: list[dict]):
    """Thread-scoped supervisor from the Good Neighbor graph."""
    from agent import coordinator as coord

    return coord.build_graph(org_id, lat, lng, history)["coordinator"]


def run_turn(thread_id: int, org_id: str, text: str, lat: float, lng: float) -> str:
    from services import threads as thread_svc

    thread_svc.add_message(thread_id, "user", text)
    agent = build_thread_agent(org_id, lat, lng, thread_svc.strands_history(thread_id))
    result = agent(text)
    reply = _text_of(result)
    thread_svc.add_message(thread_id, "assistant", reply)
    return reply
