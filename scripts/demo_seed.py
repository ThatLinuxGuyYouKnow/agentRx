"""Seed a demo reminder + run one mock search (for video/dev)."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.orchestrator import run_search
from services import reminders as reminder_svc

if __name__ == "__main__":
    outcome = run_search("atorvastatin", "20mg", 30, 40.7128, -74.0060)
    print(json.dumps(outcome.to_dict(), indent=2))
    rem = reminder_svc.set_reminder(
        "demo-user", "atorvastatin", 30, dt.date.today().isoformat()
    )
    print("reminder:", json.dumps(rem.to_dict(), indent=2))
