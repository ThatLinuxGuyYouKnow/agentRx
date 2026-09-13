"""Shared test fixtures. Tests never touch live services: strip .env keys so
load_dotenv() in server.py can't flip calle/places into live mode mid-suite."""

import pytest


@pytest.fixture(autouse=True)
def _mock_service_modes(monkeypatch):
    for var in ("CALLE_API_KEY", "CALL_E_API_KEY", "GOOGLE_PLACES_API_KEY", "DEMO_STOREFRONT_PHONE"):
        monkeypatch.delenv(var, raising=False)
