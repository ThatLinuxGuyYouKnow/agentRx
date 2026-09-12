# AgentCore deploy notes

1. Package: `agent/`, `services/`, `scripts/`, `app.py`, `requirements.txt`.
   Runtime: Python 3.11, entry `scripts/sweeper.py` (scheduled) + Streamlit
   (`app.py`) for the demo UI.
2. Env: copy `.env.example` → set `GOOGLE_PLACES_API_KEY`, `CALL_E_API_KEY`,
   Bedrock creds (`AWS_REGION`, `BEDROCK_MODEL_ID`), Telegram tokens.
   No secrets in SQLite; DB holds drug name + reminder date only.
3. Scheduler: import `infra/eventbridge-scheduler.json` — daily 09:00 UTC,
   target runs `scripts/sweeper.py` (idempotent: `notified` flag, pings only
   when due).
4. Call budget guard: max 3 calls/search, 1 search per drug/location per
   session in the UI; live test = 1–2 real stores.
