"""Good Neighbor coordinator: the Strands-native heart of agentRx.

Track: **Good Neighbor Agents** — agentRx serves a community org
(senior center / clinic / mutual-aid group). One coordinator holds the
org's watchlist (the shortage board), runs background sweeps, and does
the phone legwork. Humans approve every call plan; the LLM itself can
never dial.

Strands structure (all real ``Agent`` objects, not wrappers):

- ``finder``  — locates reputable open pharmacies near the org's members.
- ``caller``  — places CALL-E stock/price calls and fetches results.
- ``board``   — owns the watchlist: watch/unwatch, snapshots, reminders.
- ``coordinator`` — supervisor. Its tool list is the three sub-agents
  via ``Agent.as_tool()`` **plus** the same domain ``@tool`` functions
  directly, so the planner can go direct or delegate.

Two model paths, identical tools:

- Live: Bedrock (Nova-micro default, Haiku alt) drives the full
  reasoning loop when ``AWS_ACCESS_KEY_ID/SECRET`` are set.
- Offline: :class:`DeterministicModel` — a rule-based Strands
  ``Model`` provider that plans the same pipeline (intake → finder →
  caller → board summary) through the real Strands event loop, tool
  executor, and callback trace. Tests, mock mode, and the $0 demo all
  run *through Strands*, never around it.

Background operation (the "runs quietly" story): :func:`run_board_sweep`
iterates stale watches + due reminders and proposes call plans. It never
places calls — proposals wait for a human tap in the board drawer.
Wire it to EventBridge (``infra/eventbridge-scheduler.json``) or
``POST /api/sweep``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from typing import Any, AsyncGenerator

logger = logging.getLogger("agentRx.coordinator")

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DISCLAIMER = "Info only, confirm with pharmacist/doctor."

COORDINATOR_PROMPT = """You are agentRx, the refill coordinator for a community org
(senior center / clinic / mutual-aid group). You serve MANY members, not one
user: you keep a shared shortage board of watched drugs, sweep it for stale
checks and due refills, and do the pharmacy phone legwork nobody has time for.

You may: find nearby pharmacies (radius/delivery), propose call plans,
summarize the shortage board, watch/unwatch drugs, set refill reminders.
You may NEVER place phone calls yourself — call placement stays behind the
human consent gate in the app. When checks are needed, propose a call plan
(which pharmacies, which drug) and ask the coordinator to approve it.
End substantive answers with exactly: "Info only, confirm with pharmacist/doctor."
Keep replies short and practical."""

FINDER_PROMPT = """You are the finder sub-agent for a community refill board.
Locate reputable, open pharmacies near the given location. Prefer highly
rated, well-reviewed options. You never place calls."""

CALLER_PROMPT = """You are the caller sub-agent for a community refill board.
You place pharmacy stock/price check calls ONLY for pharmacy_ids already
approved through the human consent gate, then fetch their structured
results. You never invent results."""

BOARD_PROMPT = """You are the board sub-agent for a community refill board.
You own the org's watched-drug list, check snapshots, stale flags, and
refill reminders. You never place calls."""


# ---------------------------------------------------------------------------
# Domain @tool functions (single source of truth; also used by orchestrator)
# ---------------------------------------------------------------------------

def domain_tools(org_id: str, lat: float, lng: float) -> list:
    """Build fresh domain @tool functions bound to this org/location."""
    from strands import tool

    from agent import tools as base_tools
    from services import reminders as reminder_svc
    from services import watchlist as watch_svc

    @tool
    def find_pharmacies(radius_km: float = 5, delivery: bool = False) -> list[dict]:
        """Find reputable open pharmacies near the org's members."""
        return [p.to_dict() for p in base_tools.find_pharmacies(lat, lng, radius_km, delivery)]

    @tool
    def place_stock_call(pharmacy_id: str, drug: str, strength: str = "", qty: int = 30) -> str:
        """Place an approved stock/price check call. Returns call_id."""
        return base_tools.place_stock_call(pharmacy_id, drug, strength, int(qty))

    @tool
    def get_call_result(call_id: str) -> dict:
        """Fetch the structured result for a placed call."""
        return base_tools.get_call_result(call_id).to_dict()

    @tool
    def propose_call_plan(drug: str, strength: str = "", qty: int = 30,
                          radius_km: float = 5, delivery: bool = False) -> dict:
        """Finder pass only: candidate pharmacies for a drug. No calls placed.

        Returns the plan a human approves in the board drawer.
        """
        cands = base_tools.find_pharmacies(lat, lng, radius_km, delivery)
        label = f"{drug.strip()} {strength.strip()}".strip()
        return {
            "drug": label, "qty": int(qty), "radius_km": float(radius_km),
            "delivery": bool(delivery),
            "candidates": [p.to_dict() for p in cands],
            "note": "Human approval required before any call is placed.",
        }

    @tool
    def watch_drug(drug: str, strength: str = "", qty: int = 30,
                   radius_km: float = 5, delivery: bool = False,
                   check_every_days: int = 7) -> dict:
        """Add a drug to the org's shortage board with a re-check cadence
        in days (e.g. 5 = "we usually run out every 5 days, check that often")."""
        return watch_svc.add_watch(org_id, drug, strength, qty, radius_km,
                                   delivery, check_every_days)

    @tool
    def unwatch_drug(drug: str) -> str:
        """Remove a drug from the shortage board by name."""
        for w in watch_svc.list_watches(org_id):
            if w["drug"].lower() == drug.strip().lower():
                watch_svc.remove_watch(w["id"], org_id)
                return f"stopped watching {w['drug']}"
        return f"{drug} was not on the shortage board"

    @tool
    def board_summary() -> list[dict]:
        """Current board: watches + latest snapshots + stale flags."""
        return watch_svc.board(org_id)

    @tool
    def set_reminder(user_id: str, drug: str, days_supply: int, last_date: str) -> dict:
        """Set a member refill reminder (remind = last_date + days_supply - 7)."""
        return reminder_svc.set_reminder(user_id, drug, days_supply, last_date).to_dict()

    @tool
    def due_and_stale() -> dict:
        """Background-sweep view: due reminders + stale watches needing re-check."""
        return {
            "due_reminders": [r.to_dict() for r in reminder_svc.due_reminders()],
            "stale_watches": watch_svc.stale_watches(org_id),
        }

    return [find_pharmacies, place_stock_call, get_call_result, propose_call_plan,
            watch_drug, unwatch_drug, board_summary, set_reminder, due_and_stale]


# ---------------------------------------------------------------------------
# DeterministicModel: offline rule-based Strands model provider
# ---------------------------------------------------------------------------

class DeterministicModel:
    """Rule-based Strands model so the full agent loop runs with $0 spend.

    Implements the Strands ``Model`` interface over the Bedrock streaming
    event shapes (``messageStart`` / ``contentBlockStart|Delta|Stop`` /
    ``messageStop``). On each event-loop turn it inspects the conversation
    and emits the next step of the Good Neighbor pipeline:

    - watch/unwatch/remind/board intents → matching board tool → confirm
    - drug check intent → ``find_pharmacies`` → ``place_stock_call`` × N →
      ``get_call_result`` × N → cheapest-first summary + disclaimer

    The event loop executes real tools; this class only *plans*.
    """

    def __init__(self, org_id: str = "demo-org", lat: float = 40.7128,
                 lng: float = -74.0060) -> None:
        self._config: dict[str, Any] = {"model_id": "agentrx-deterministic-1",
                                        "org_id": org_id}
        self.lat = lat
        self.lng = lng

    @property
    def stateful(self) -> bool:
        return False

    # -- Model interface ----------------------------------------------------
    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        yield {"output": None}
        return

    def _text_of_blocks(self, blocks: list) -> str:
        out = []
        for b in blocks or []:
            if isinstance(b, dict) and "text" in b:
                out.append(str(b["text"]))
        return " ".join(out)

    def _scan(self, messages) -> dict[str, Any]:
        """Collect user text + executed tool uses/results from history."""
        user_texts: list[str] = []
        uses: list[dict] = []          # {id, name, input}
        results: dict[str, Any] = {}   # toolUseId -> result content
        for m in messages or []:
            role = m.get("role")
            content = m.get("content", [])
            if role == "user":
                t = self._text_of_blocks(content)
                if t:
                    user_texts.append(t)
                for b in content:
                    if isinstance(b, dict) and "toolResult" in b:
                        tr = b["toolResult"]
                        results[tr.get("toolUseId", "")] = tr.get("content", {})
            elif role == "assistant":
                for b in content or []:
                    if isinstance(b, dict) and "toolUse" in b:
                        tu = b["toolUse"]
                        uses.append({"id": tu.get("toolUseId", ""),
                                     "name": tu.get("name", ""),
                                     "input": tu.get("input", {}) or {}})
        return {"user_texts": user_texts, "uses": uses, "results": results}

    @staticmethod
    def _as_json(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                pass
            # tool results sometimes stringify python objects; try literal parse
            try:
                import ast
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                pass
        return value

    def _result_json(self, results: dict, use_id: str) -> Any:
        raw = results.get(use_id, {})
        if isinstance(raw, list):  # [ {"text"|"json": ...}, ... ] content blocks
            parts = []
            for b in raw:
                if isinstance(b, dict):
                    if "json" in b:
                        parts.append(b["json"])
                    elif "text" in b:
                        parts.append(self._as_json(b["text"]))
            if len(parts) == 1:
                return parts[0]
            return parts or raw
        if isinstance(raw, dict):
            # strands wraps tool output as {"text": ...} or {"json": ...}
            if "json" in raw:
                return raw["json"]
            txt = raw.get("text", "")
            if isinstance(txt, (dict, list)):
                return txt
            parsed = self._as_json(txt)
            return parsed
        return self._as_json(raw)

    def _parse_drug(self, text: str) -> dict[str, Any]:
        qty = 30
        m = re.search(r"\s*x\s*(\d+)\s*(tabs?|tablets?)?\s*$", text, re.I)
        body = text
        if m:
            qty = int(m.group(1))
            body = text[: m.start()].strip()
        strength = ""
        m2 = re.search(r"(\d+\s?(?:mg|g|mcg|ml|%|iu))\b", body, re.I)
        drug = body
        if m2:
            strength = m2.group(1).replace(" ", "")
            drug = (body[: m2.start()] + " " + body[m2.end():]).strip()
            drug = re.sub(r"\s+", " ", drug)
        radius = 5.0
        m3 = re.search(r"(\d+(?:\.\d+)?)\s?km", text, re.I)
        if m3:
            radius = float(m3.group(1))
        delivery = bool(re.search(r"deliver", text, re.I))
        return {"drug": drug.strip(), "strength": strength,
                "qty": qty, "radius_km": radius, "delivery": delivery}

    def _plan(self, messages) -> tuple[str, Any]:
        """Return (kind, payload): tool_uses list or final text."""
        scan = self._scan(messages)
        user_text = scan["user_texts"][-1] if scan["user_texts"] else ""
        low = user_text.lower()
        uses = scan["uses"]
        results = scan["results"]
        done = {u["name"] for u in uses}

        def out_for(name: str) -> list[Any]:
            return [self._result_json(results, u["id"])
                    for u in uses if u["name"] == name and u["id"] in results]

        # -- board intents (single-tool turns) -------------------------------
        if re.match(r"^(watch|unwatch|stop watching)\b", low):
            if "watch_drug" in done or "unwatch_drug" in done:
                return ("text", self._confirm_text(low, out_for("watch_drug") + out_for("unwatch_drug")))
            if low.startswith("unwatch") or low.startswith("stop"):
                drug = re.sub(r"^(unwatch|stop watching)\s+", "", user_text).strip()
                return ("tools", [("unwatch_drug", {"drug": drug})])
            p = self._parse_drug(re.sub(r"^watch\s+", "", user_text))
            p["drug"] = re.sub(r"\s+every\s+\d+\s*days?\s*$", "", p["drug"],
                               flags=re.I).strip()
            return ("tools", [("watch_drug", {"drug": p["drug"], "strength": p["strength"],
                                              "qty": p["qty"], "radius_km": p["radius_km"],
                                              "delivery": p["delivery"],
                                              "check_every_days": self._parse_cadence(user_text)})])
        if "remind" in low and ("set" in low or "refill" in low or "remind" == low.split()[0]):
            if "set_reminder" in done:
                return ("text", self._confirm_text(low, out_for("set_reminder")))
            p = self._parse_drug(user_text)
            import datetime as dt
            return ("tools", [("set_reminder", {"user_id": "demo-member",
                                                "drug": p["drug"] or user_text.strip(),
                                                "days_supply": 30,
                                                "last_date": dt.date.today().isoformat()})])
        if low.strip() in ("board", "shortage board") or "shortage" in low or "stale" in low:
            if "board_summary" in done or "due_and_stale" in done:
                return ("text", self._board_text(out_for("board_summary") + out_for("due_and_stale")))
            return ("tools", [("board_summary", {})])

        # -- drug check pipeline ---------------------------------------------
        finds = [u for u in uses if u["name"] == "find_pharmacies"]
        places = [u for u in uses if u["name"] == "place_stock_call"]
        gets = [u for u in uses if u["name"] == "get_call_result"]

        if not finds:
            p = self._parse_drug(user_text)
            if not p["drug"]:
                return ("text", "Tell me the medication (e.g. atorvastatin 20mg x30) "
                                 "and I'll line up nearby pharmacies for the board. "
                                 + DISCLAIMER)
            return ("tools", [("find_pharmacies", {"radius_km": p["radius_km"],
                                                   "delivery": p["delivery"]})])

        pharmacies: list[dict] = []
        for payload in out_for("find_pharmacies"):
            if isinstance(payload, list):
                pharmacies.extend([x for x in payload if isinstance(x, dict)])
        # map executed place calls -> call_ids
        call_ids: list[str] = []
        for payload in out_for("place_stock_call"):
            cid = payload if isinstance(payload, str) else self._coerce_call_id(payload)
            if cid:
                call_ids.append(cid)
        if len(places) < len(pharmacies) and len(pharmacies) > 0 and len(places) < 3:
            p = self._parse_drug(user_text)
            nxt = [ph for ph in pharmacies[:3]
                   if ph.get("pharmacy_id") not in
                   {u["input"].get("pharmacy_id") for u in places}]
            if nxt:
                return ("tools", [("place_stock_call",
                                    {"pharmacy_id": ph["pharmacy_id"], "drug": p["drug"],
                                     "strength": p["strength"], "qty": p["qty"]})
                                   for ph in nxt[: 3 - len(places)]])
        if len(gets) < len(call_ids):
            pending = call_ids[len(gets):]
            return ("tools", [("get_call_result", {"call_id": cid}) for cid in pending])
        if gets or (finds and not pharmacies):
            return ("text", self._results_text(user_text, out_for("get_call_result")))
        # find executed but results not yet visible (shouldn't happen) — wait politely
        return ("text", "Checking pharmacies for the board now… " + DISCLAIMER)

    @staticmethod
    def _coerce_call_id(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for k in ("call_id", "id", "text"):
                v = payload.get(k)
                if isinstance(v, str) and v:
                    return v
        return ""

    @staticmethod
    def _parse_cadence(text: str) -> int:
        """"every 5 days" -> 5; daily/weekly/monthly shortcuts; default 7."""
        m = re.search(r"every\s+(\d+)\s*days?", text, re.I)
        if m:
            return max(1, min(90, int(m.group(1))))
        low = text.lower()
        if "daily" in low or "every day" in low:
            return 1
        if "weekly" in low or "every week" in low:
            return 7
        if "monthly" in low or "every month" in low:
            return 30
        return 7

    def _confirm_text(self, low: str, outs: list) -> str:
        detail = ""
        if outs and isinstance(outs[0], dict):
            d = outs[0]
            detail = f" ({d.get('drug', '')} {d.get('strength', '')}".strip() + ")"
        if low.startswith("unwatch") or low.startswith("stop"):
            return f"Removed from the shortage board{detail}. {DISCLAIMER}"
        every = ""
        if outs and isinstance(outs[0], dict) and outs[0].get("check_every_days"):
            every = f", re-checking every {outs[0]['check_every_days']} days"
        return (f"On the shortage board{detail}{every} — I'll flag it when it's "
                f"due for a re-check. {DISCLAIMER}")

    def _board_text(self, outs: list) -> str:
        rows: list = outs[0] if outs else []
        if not isinstance(rows, list) or not rows:
            rows = (outs[0].get("stale_watches", []) if isinstance(outs[0], dict) else []) if outs else []
        if not rows:
            return "The shortage board is empty. Say 'watch lisinopril 10mg' and I'll track it. " + DISCLAIMER
        lines = []
        for r in rows[:10]:
            w = r.get("watch", r) if isinstance(r, dict) else {}
            stale = "stale — re-check?" if r.get("stale") else "fresh"
            every = r.get("interval_days") or (w.get("check_every_days") if isinstance(w, dict) else None) or 7
            due = ""
            if r.get("next_check"):
                due = f", next due {r['next_check']}"
            lines.append(f"- {w.get('drug', '?')} {w.get('strength', '')}: {stale} (every {every}d{due})".rstrip())
        return "Shortage board:\n" + "\n".join(lines) + "\n" + DISCLAIMER

    def _results_text(self, user_text: str, result_payloads: list) -> str:
        label = self._parse_drug(user_text).get("drug") or user_text.strip() or "this drug"
        parsed = []
        for payload in result_payloads:
            r = payload if isinstance(payload, dict) else self._as_json(payload)
            if isinstance(r, dict) and "pharmacy" in r:
                parsed.append(r)
        in_stock = [r for r in parsed if r.get("in_stock")]
        if not parsed:
            return (f"No pharmacy results came back for {label} — the board keeps "
                    f"watching it. {DISCLAIMER}")
        if not in_stock:
            return (f"None of the {len(parsed)} pharmacies checked have {label} right "
                    f"now. It's on the shortage board — I'll flag it for re-check. {DISCLAIMER}")
        in_stock.sort(key=lambda r: (r.get("price") is None, r.get("price") or 0))
        best = in_stock[0]
        name = best.get("pharmacy", {}).get("name", "A pharmacy")
        price = best.get("price")
        detail = f"${price:.2f}" if price is not None else "price unknown"
        if best.get("pickup_time"):
            detail += f", ready {best['pickup_time']}"
        extra = ""
        if len(in_stock) > 1:
            extra = f" ({len(in_stock)} of {len(parsed)} in stock)"
        return (f"Good news — {name} has {label} in stock at {detail}{extra}. "
                f"Board updated. {DISCLAIMER}")

    async def stream(self, messages, tool_specs=None, system_prompt=None, *,
                     tool_choice=None, system_prompt_content=None,
                     invocation_state=None, cancel_signal=None, **kwargs):
        kind, payload = self._plan(messages)
        yield {"messageStart": {"role": "assistant"}}
        if kind == "tools":
            for name, args in payload:
                yield {"contentBlockStart": {"start": {"toolUse": {
                    "toolUseId": f"offline-{uuid.uuid4().hex[:8]}", "name": name}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {
                    "input": json.dumps(args)}}}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": str(payload)}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}}}


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def _model(live: bool, org_id: str, lat: float, lng: float):
    if live:
        from strands.models import BedrockModel
        return BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
    return DeterministicModel(org_id=org_id, lat=lat, lng=lng)


def build_graph(org_id: str = "demo-org", lat: float = 40.7128,
                lng: float = -74.0060, history: list | None = None,
                live: bool | None = None) -> dict[str, Any]:
    """Build the Good Neighbor multi-agent graph.

    Returns ``{"finder", "caller", "board", "coordinator", "live", "tools"}``.
    ``live`` autodetects Bedrock creds unless forced.
    """
    from strands import Agent

    if live is None:
        live = bool(os.environ.get("AWS_ACCESS_KEY_ID")
                    and os.environ.get("AWS_SECRET_ACCESS_KEY"))
    tools = domain_tools(org_id, lat, lng)
    by_name = {getattr(t, "tool_name", getattr(t, "__name__", "")): t for t in tools}

    def sub(name: str, prompt: str, wanted: list[str]):
        return Agent(
            name=name,
            description=prompt.split("\n")[0][:120],
            model=_model(live, org_id, lat, lng),
            system_prompt=prompt,
            tools=[by_name[w] for w in wanted if w in by_name],
            state={"org_id": org_id, "lat": lat, "lng": lng, "sub_agent": name},
        )

    finder = sub("finder", FINDER_PROMPT, ["find_pharmacies", "propose_call_plan"])
    caller = sub("caller", CALLER_PROMPT, ["place_stock_call", "get_call_result"])
    board = sub("board", BOARD_PROMPT, ["watch_drug", "unwatch_drug", "board_summary",
                                        "set_reminder", "due_and_stale"])
    coordinator = Agent(
        name="coordinator",
        description="Good Neighbor refill coordinator for community orgs",
        model=_model(live, org_id, lat, lng),
        messages=list(history or []),
        system_prompt=COORDINATOR_PROMPT,
        tools=[*tools,
               finder.as_tool(description="Delegate pharmacy search to the finder sub-agent."),
               caller.as_tool(description="Delegate approved calls to the caller sub-agent."),
               board.as_tool(description="Delegate board/reminder work to the board sub-agent.")],
        state={"org_id": org_id, "lat": lat, "lng": lng, "track": "good-neighbor"},
    )
    return {"finder": finder, "caller": caller, "board": board,
            "coordinator": coordinator, "live": live, "tools": tools}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

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


def run_coordinator_turn(thread_id: int, org_id: str, text: str,
                         lat: float, lng: float,
                         callback_handler=None) -> dict[str, Any]:
    """One Strands-driven turn with SQLite-backed memory.

    Returns ``{"reply", "live", "trace"}`` where trace is the callback
    event list (tool uses) for the demo/judging story.
    """
    from services import threads as thread_svc

    thread_svc.add_message(thread_id, "user", text)
    graph = build_graph(org_id, lat, lng, thread_svc.strands_history(thread_id))
    agent = graph["coordinator"]
    result = agent(text)
    reply = _text_of(result)
    thread_svc.add_message(thread_id, "assistant", reply)
    trace = [{"tool": b["toolUse"]["name"],
              "input": json.dumps(b["toolUse"].get("input", {}))[:200]}
             for m in agent.messages for b in (m.get("content") or [])
             if isinstance(b, dict) and "toolUse" in b]
    if callback_handler is not None:
        try:
            callback_handler({"trace": trace, "reply": reply})
        except Exception:
            pass
    return {"reply": reply, "live": graph["live"], "trace": trace}


def run_board_sweep(org_id: str, lat: float = 40.7128,
                    lng: float = -74.0060) -> dict[str, Any]:
    """Background pass for the org: due reminders + stale watches + call plans.

    Never places calls. Returns a report the board drawer renders and
    EventBridge can trigger daily.
    """
    from agent import tools as base_tools
    from services import reminders as reminder_svc
    from services import watchlist as watch_svc

    due = [r.to_dict() for r in reminder_svc.due_reminders()]
    stale = watch_svc.stale_watches(org_id)
    plans = []
    for row in stale[:10]:
        w = row["watch"]
        try:
            cands = base_tools.find_pharmacies(lat, lng, float(w.get("radius_km", 5)),
                                               bool(w.get("delivery", 0)))
        except Exception as e:  # sweep must never crash the schedule
            logger.warning("sweep finder failed for watch %s: %s", w.get("id"), e)
            continue
        label = f"{w.get('drug', '')} {w.get('strength', '')}".strip()
        plans.append({"watch_id": w.get("id"), "drug": label,
                      "candidates": [p.to_dict() for p in cands],
                      "note": "Approve in the board drawer to place calls."})
    logger.info("board sweep org=%s due=%d stale=%d plans=%d",
                org_id, len(due), len(stale), len(plans))
    return {"org_id": org_id, "due_reminders": due,
            "stale_watches": [r["watch"] for r in stale], "call_plans": plans}


def is_live() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID")
                and os.environ.get("AWS_SECRET_ACCESS_KEY"))
