"""User-facing clocks: America/New_York. Journals stay UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def parse_utc(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e16:
            v = v / 1e9
        elif v > 1e14:
            v = v / 1e6
        elif v > 1e11:
            v = v / 1e3
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def date_et(value) -> str:
    """Calendar date in America/New_York: '2026-08-23'. Empty if unparseable."""
    dt = parse_utc(value)
    if dt is None:
        return ""
    return dt.astimezone(ET).strftime("%Y-%m-%d")


def format_et(value, *, timespec: str = "minutes") -> str:
    """Human clock: '2026-08-21 6:00 PM ET'. Empty if unparseable."""
    dt = parse_utc(value)
    if dt is None:
        return ""
    local = dt.astimezone(ET)
    hour12 = local.strftime("%-I:%M %p") if timespec == "minutes" else local.strftime("%-I:%M:%S %p")
    return f"{local.strftime('%Y-%m-%d')} {hour12} ET"


def next_4h_close(now: datetime | None = None) -> datetime:
    """Next Coinbase 4h bucket close (UTC hours 0/4/8/12/16/20)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    h = (now.hour // 4 + 1) * 4
    if h >= 24:
        nxt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        nxt = now.replace(hour=h, minute=0, second=0, microsecond=0)
    return nxt


def next_4h_close_et(now: datetime | None = None) -> str:
    return format_et(next_4h_close(now))
