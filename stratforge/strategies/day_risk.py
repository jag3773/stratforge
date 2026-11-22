"""UTC-day mark-to-market loss tracker.

daily_loss_usd = max(0, day_start_equity - current_equity).
day_start_equity is frozen on the first mark of each UTC date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


def utc_today(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date()


@dataclass
class DayRiskTracker:
    day_utc: date | None = None
    day_start_equity: float | None = None
    daily_realized_pnl: float = 0.0

    def roll(self, equity: float, now: datetime | None = None) -> bool:
        """Return True when a new UTC day opened (caller should journal DAY_OPEN)."""
        today = utc_today(now)
        if self.day_utc == today and self.day_start_equity is not None:
            return False
        self.day_utc = today
        self.day_start_equity = float(equity)
        self.daily_realized_pnl = 0.0
        return True

    def daily_loss_usd(self, equity: float) -> float:
        if self.day_start_equity is None:
            return 0.0
        return max(0.0, float(self.day_start_equity) - float(equity))

    def add_realized(self, pnl: float) -> None:
        self.daily_realized_pnl += float(pnl)

    def snapshot(self) -> dict:
        return {
            "day_utc": self.day_utc.isoformat() if self.day_utc else None,
            "day_start_equity": (
                None if self.day_start_equity is None else round(float(self.day_start_equity), 6)
            ),
            "daily_realized_pnl": round(float(self.daily_realized_pnl), 6),
        }
