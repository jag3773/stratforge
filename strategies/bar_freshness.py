"""Refuse to trade a 4h bar that is too old or from the future.

Bar `ts` is bucket-open nanoseconds (bucket_ms * 1_000_000). Close = open + 4h.
"""
from __future__ import annotations

import os
import time
from typing import Any

BAR_MINUTES = 240
# After the official close, allow this much slack for 50-symbol fold + restart.
MAX_LAG_SEC = float(os.getenv("BAR_MAX_LAG_SEC", str(20 * 60)))
FUTURE_SLACK_SEC = float(os.getenv("BAR_FUTURE_SLACK_SEC", "120"))


def bar_open_unix(bar: dict[str, Any]) -> float:
    ts = bar.get("ts") or 0
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return 0.0
    if ts > 1e16:  # ns
        return ts / 1e9
    if ts > 1e13:  # us
        return ts / 1e6
    if ts > 1e11:  # ms
        return ts / 1e3
    return ts


def bar_close_unix(bar: dict[str, Any], bar_minutes: int = BAR_MINUTES) -> float:
    return bar_open_unix(bar) + float(bar_minutes) * 60.0


def bar_tradable(
    bar: dict[str, Any],
    *,
    now: float | None = None,
    max_lag_sec: float = MAX_LAG_SEC,
    future_slack_sec: float = FUTURE_SLACK_SEC,
    bar_minutes: int = BAR_MINUTES,
) -> tuple[bool, str]:
    now_s = time.time() if now is None else float(now)
    close = bar_close_unix(bar, bar_minutes=bar_minutes)
    if close <= 0:
        return False, "bar_ts_unparsed"
    if now_s + future_slack_sec < close:
        return False, f"bar_from_future close={close:.0f} now={now_s:.0f}"
    lag = now_s - close
    if lag > max_lag_sec:
        return False, f"stale_bar lag_sec={lag:.0f} max={max_lag_sec:.0f}"
    return True, "ok"
