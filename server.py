"""agentRx API server: serves the web UI + JSON API over the same orchestrator.

Run:  ~/myvenv/bin/python server.py        (or: uvicorn server:app --port 8000)
UI:   http://localhost:8000/
"""

from __future__ import annotations

import logging
import os

logging.basicConfig(
    level=getattr(logging, os.environ.get("AGENTRX_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from agent.orchestrator import DISCLAIMER, check_stock, find_candidates, run_search
from services import reminders as reminder_svc
from services import threads as thread_svc
from services import watchlist as watch_svc

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = FastAPI(title="agentRx")


class SearchRequest(BaseModel):
    drug: str
    strength: str = ""
    qty: int = 30
    lat: float = 40.7128
    lng: float = -74.0060


class ReminderRequest(BaseModel):
    user_id: str = "demo-user"
    drug: str
    days_supply: int = 30
    last_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.post("/api/search")
def api_search(req: SearchRequest):
    outcome = run_search(req.drug, req.strength, req.qty, req.lat, req.lng)
    reminder_svc.save_run(req.drug, req.strength, req.qty, req.lat, req.lng, outcome.to_dict())
    return {**outcome.to_dict(), "disclaimer": DISCLAIMER}


class CandidateRequest(BaseModel):
    lat: float = 40.7128
    lng: float = -74.0060
    radius_km: float = 5
    delivery: bool = False


@app.post("/api/pharmacies")
def api_pharmacies(req: CandidateRequest):
    """Step 1: finder only — no calls placed. User approves before step 2."""
    cands = find_candidates(req.lat, req.lng, req.radius_km, req.delivery)
    return {
        "pharmacies": [p.to_dict() for p in cands],
        "disclaimer": DISCLAIMER,
    }


class CheckRequest(BaseModel):
    drug: str
    strength: str = ""
    qty: int = 30
    lat: float = 40.7128
    lng: float = -74.0060
    pharmacies: list[dict] = Field(default_factory=list)
    watch_id: int | None = None


@app.post("/api/check")
def api_check(req: CheckRequest):
    """Step 2: place CALL-E calls for the user-approved subset only."""
    from agent.models import Pharmacy

    approved = []
    for p in req.pharmacies:
        try:
            approved.append(Pharmacy(**{k: p[k] for k in Pharmacy.__dataclass_fields__ if k in p}))
        except (TypeError, KeyError):
            continue
    outcome = check_stock(req.drug, req.strength, req.qty, approved)
    reminder_svc.save_run(req.drug, req.strength, req.qty, req.lat, req.lng, outcome.to_dict())
    if req.watch_id is not None:
        label = f"{req.drug} {req.strength} x{req.qty}".strip()
        watch_svc.save_snapshot(req.watch_id, {"drug": label, **outcome.to_dict()})
    return {**outcome.to_dict(), "disclaimer": DISCLAIMER}


class EmailSummaryRequest(BaseModel):
    to_email: str
    drug: str
    summary: str = ""


@app.get("/api/calls/{call_id}/transcript")
def api_transcript(call_id: str):
    """Transcript text for the in-app viewer (mock dialogue or live turns)."""
    from services import calle as calle_mod

    text = calle_mod.get_transcript_text(call_id)
    if text is None:
        raise HTTPException(404, "transcript not available")
    return {"call_id": call_id, "transcript": text}
@app.post("/api/email-summary")
def api_email_summary(req: EmailSummaryRequest):
    """Email the comparison summary to the user/caregiver (needs SMTP_* env)."""
    import smtplib
    from email.message import EmailMessage

    host = os.environ.get("SMTP_HOST")
    if not host:
        raise HTTPException(
            501, "email not configured: set SMTP_HOST/PORT/USER/PASS/FROM to enable"
        )
    msg = EmailMessage()
    msg["Subject"] = f"agentRx stock check: {req.drug}"
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "agentrx@localhost"))
    msg["To"] = req.to_email
    msg.set_content(
        f"agentRx stock check for {req.drug}\n\n{req.summary}\n\n{DISCLAIMER}"
    )
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(msg)
    return {"emailed": req.to_email}


@app.post("/api/reminders")
def api_create_reminder(req: ReminderRequest):
    return reminder_svc.set_reminder(req.user_id, req.drug, req.days_supply, req.last_date).to_dict()


@app.get("/api/reminders")
def api_list_reminders(user_id: str = "demo-user"):
    return [r.to_dict() for r in reminder_svc.list_reminders(user_id)]


@app.delete("/api/reminders/{reminder_id}")
def api_delete_reminder(reminder_id: int):
    if not reminder_svc.delete_reminder(reminder_id):
        raise HTTPException(404, "reminder not found")
    return {"deleted": reminder_id}


@app.post("/api/sweep")
def api_sweep():
    from agent import coordinator as coord_mod

    return {
        "reminder_sweep": reminder_svc.sweep(),
        "stale_watches": {
            org: watch_svc.stale_watches(org) for org in _orgs_with_watches()
        },
        # Good Neighbor background pass: call plans proposed, never placed.
        "board_sweep": {
            org: coord_mod.run_board_sweep(org) for org in _orgs_with_watches()
        },
    }


def _orgs_with_watches() -> list[str]:
    from services import db as db_mod

    with db_mod.connect() as conn:
        rows = conn.execute("SELECT DISTINCT org_id FROM watched").fetchall()
    return [r["org_id"] for r in rows]


class ThreadCreate(BaseModel):
    org_id: str = "demo-org"
    title: str = "conversation"


@app.post("/api/threads")
def api_create_thread(req: ThreadCreate):
    return thread_svc.create_thread(req.org_id, req.title)


@app.get("/api/threads")
def api_list_threads(org_id: str = "demo-org"):
    return thread_svc.list_threads(org_id)


@app.get("/api/threads/{thread_id}/messages")
def api_thread_messages(thread_id: int):
    return thread_svc.get_messages(thread_id)


class TurnRequest(BaseModel):
    text: str
    org_id: str = "demo-org"
    lat: float = 40.7128
    lng: float = -74.0060


@app.post("/api/threads/{thread_id}/messages")
def api_thread_turn(thread_id: int, req: TurnRequest):
    from agent.assistant import run_turn

    try:
        reply = run_turn(thread_id, req.org_id, req.text, req.lat, req.lng)
    except Exception as e:
        raise HTTPException(502, f"agent turn failed: {e}")
    return {"reply": reply}


class WatchRequest(BaseModel):
    org_id: str = "demo-org"
    drug: str
    strength: str = ""
    qty: int = 30
    radius_km: float = 5
    delivery: bool = False
    check_every_days: int = 7


@app.post("/api/watchlist")
def api_add_watch(req: WatchRequest):
    return watch_svc.add_watch(req.org_id, req.drug, req.strength, req.qty,
                               req.radius_km, req.delivery, req.check_every_days)


@app.get("/api/watchlist")
def api_list_watchlist(org_id: str = "demo-org"):
    return watch_svc.list_watches(org_id)


@app.delete("/api/watchlist/{watch_id}")
def api_remove_watch(watch_id: int, org_id: str = "demo-org"):
    if not watch_svc.remove_watch(watch_id, org_id):
        raise HTTPException(404, "watch not found")
    return {"deleted": watch_id}


class WatchUpdate(BaseModel):
    check_every_days: int


@app.patch("/api/watchlist/{watch_id}")
def api_update_watch(watch_id: int, req: WatchUpdate, org_id: str = "demo-org"):
    """Edit a watch's re-check cadence (e.g. every 5 days)."""
    row = watch_svc.update_watch(watch_id, org_id, req.check_every_days)
    if row is None:
        raise HTTPException(404, "watch not found")
    return row


@app.get("/api/board")
def api_board(org_id: str = "demo-org"):
    return watch_svc.board(org_id)


@app.get("/api/config")
def api_config():
    """Public client config (CARTO key is a usage-tracked basemap key, not a secret)."""
    from services.calle import force_mock

    mocked = force_mock()
    calle_key = os.environ.get("CALLE_API_KEY") or os.environ.get("CALL_E_API_KEY")
    return {
        "cartoApiKey": os.environ.get("CARTO_API_KEY", ""),
        "calleMode": "mock" if (mocked or not calle_key) else "live",
        "mock": mocked,
        "demoStorefront": bool(os.environ.get("DEMO_STOREFRONT_PHONE")),
    }


if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
