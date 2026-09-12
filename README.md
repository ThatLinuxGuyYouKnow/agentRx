# agentRx

Type a drug once → pick radius / delivery → approve pharmacies → agentRx
calls them via CALL-E (or emails you the list) → comparison table → refill
reminder. No auto-ordering, no medical advice. **No calls are placed until
you approve the pharmacy list.**

Targets: **Agents for Humans → Everyday Agents** (Strands + AgentCore),
**CALL-E → Most Practical Use Case** (real phone calls).

## Quickstart (mock mode, $0 spend)

```bash
cp .env.example .env
~/myvenv/bin/python -m pytest tests/ -q
~/myvenv/bin/python scripts/demo_seed.py
~/myvenv/bin/python server.py          # web UI -> http://localhost:8000/
~/myvenv/bin/python -m streamlit run app.py   # alt: spec Streamlit page
```

Mock mode (no keys set): deterministic mock pharmacies + mock CALL-E
results. For the video: replace the 3rd mock number with your own phone.

## Going live (spend only here)

1. `GOOGLE_PLACES_API_KEY` → real top-3 (rating≥4.0, >50 reviews, open now, 5km).
2. `CALL_E_API_KEY` (+ `CALL_E_BASE_URL`) → real parallel calls. Budget: **20
   free calls** — 2 mock numbers + your own phone for the video, 1–2 real
   stores max for the final test.
3. `AWS_ACCESS_KEY_ID/SECRET` → Strands agent reasons over the same tools on
   Bedrock (`BEDROCK_MODEL_ID`, default `amazon.nova-micro-v1:0`,
   alt `anthropic.claude-3-haiku-20240307-v1:0`). Without creds the
   orchestrator runs deterministically (no LLM).
4. `TELEGRAM_BOT_TOKEN/CHAT_ID` → refill pings via Telegram, else console.

## Architecture

Finder → Caller (fan-out, 3 parallel) → Aggregator. Handoff to human if
all out-of-stock or all calls fail.

- `agent/tools.py` — Strands contracts: `find_pharmacies`,
  `place_stock_call`, `get_call_result`, `set_reminder`
- `agent/orchestrator.py` — fan-out/fan-in + sorting (cheapest in-stock first)
- `agent/strands_agent.py` — Bedrock agent exposing the same tools
- `services/places.py`, `services/calle.py`, `services/reminders.py`, `services/db.py`
- `app.py` — 1-page Streamlit UI (table + transcripts + reminder status)
- `server.py` + `web/` — demo web UI: dark Leaflet map (cairn pattern:
  OSM/CARTO tiles, pulse user dot, status pins, dashed route to cheapest
  in-stock) + paper chat. Flow: drug → radius chips (1/2/5/10 km) + delivery
  toggle → candidates on map + tap-to-deselect list → consent gate
  (`POST /api/pharmacies` then `POST /api/check`) → results. ✉️ emails the
  list/results to your address (`POST /api/email-summary`, needs SMTP_* env).
  Chat parses e.g. `atorvastatin 20mg x30`; browser geolocation with NYC fallback.
  Long back-and-forths: coordinator chat persists in threads
  (`POST /api/threads/{id}/messages` runs the Strands agent with full history;
  call placement stays behind the consent gate). 📋 board drawer: watchlist
  matrix with stale flags (`/api/watchlist`, `/api/board`); re-check jumps
  back into the consent flow and snapshots feed the board.
- `scripts/sweeper.py` — EventBridge daily target; pings only when
  `remind_date (last + supply − 7) <= today`

## Deploy (AgentCore Runtime + EventBridge Scheduler)

See `infra/`. Sweeper schedule: `cron(0 9 * * ? *)` →
`scripts/sweeper.py`. SQLite local; per-user delete button in UI.

## Scope guards (explicit)

- No ordering / payment / Rx submission. No Rx numbers stored.
- No allergy / interaction / dosage advice — static disclaimer only:
  "Info only, confirm with pharmacist/doctor."
- Minimal data: drug name + reminder date only. US demo only.
- Voice input is P2 stretch: browser mic → **Amazon Transcribe** (AWS STT;
  there is no Bedrock STT) → autofill text field; Bedrock Nova/Claude is the LLM.

Note: pinned Python 3.11 in spec; dev env here is 3.12 — code is version-clean.
