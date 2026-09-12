"""EventBridge daily sweeper entrypoint. Pings only when reminders are due."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.reminders import sweep

if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(sweep(today), indent=2))
