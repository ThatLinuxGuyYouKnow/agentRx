"""Shared dataclasses for agentRx. No PHI beyond drug name + reminder date."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Pharmacy:
    pharmacy_id: str
    name: str
    address: str
    phone: str
    rating: float
    user_ratings_total: int
    open_now: bool
    lat: float
    lng: float
    distance_km: float = 0.0
    delivery: bool = False  # True when found via delivery-oriented search

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckResult:
    pharmacy: Pharmacy
    in_stock: bool
    price: Optional[float]  # cash price USD, None if unknown/out of stock
    pickup_time: Optional[str]  # e.g. "today 4pm", None if unknown
    transcript_url: Optional[str]
    call_id: str
    call_status: str = "completed"  # completed | failed | no-answer
    raw_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pharmacy"] = self.pharmacy.to_dict()
        return d


@dataclass
class Reminder:
    user_id: str
    drug: str
    days_supply: int
    last_date: str  # YYYY-MM-DD
    remind_date: str  # YYYY-MM-DD, computed = last_date + days_supply - 7
    id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchOutcome:
    """Aggregator output: comparison table + handoff flag."""

    results: list[CheckResult] = field(default_factory=list)
    needs_human: bool = False
    handoff_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "needs_human": self.needs_human,
            "handoff_reason": self.handoff_reason,
        }
