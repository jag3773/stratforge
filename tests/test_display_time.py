#!/usr/bin/env python3
"""User-facing clocks are America/New_York. Journals stay UTC."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.display_time import (  # noqa: E402
    date_et,
    format_et,
    next_4h_close_et,
)


def test_format_et_summer_iso():
    # 22:00 UTC 21 Aug 2026 = 6:00 PM EDT
    s = format_et("2026-08-21T22:00:00+00:00")
    assert "UTC" not in s
    assert "+00:00" not in s
    assert s == "2026-08-21 6:00 PM ET"
    print("ok summer ISO")


def test_format_et_winter_iso():
    # 22:00 UTC 21 Jan 2026 = 5:00 PM EST
    s = format_et("2026-01-21T22:00:00+00:00")
    assert s == "2026-01-21 5:00 PM ET"
    print("ok winter ISO")


def test_format_et_z_and_naive():
    assert format_et("2026-08-21T20:00:00Z") == "2026-08-21 4:00 PM ET"
    dt = datetime(2026, 8, 21, 20, 0, 0)  # naive = UTC
    assert format_et(dt) == "2026-08-21 4:00 PM ET"
    print("ok Z and naive")


def test_format_et_none():
    assert format_et(None) == ""
    assert format_et("") == ""
    print("ok empty")


def test_date_et_first_live_fill():
    # First the live sleeve LIVE fill 08:00 UTC 23 Aug 2026 = 4:00 AM EDT
    assert date_et("2026-08-23T08:00:35.131248+00:00") == "2026-08-23"
    # UTC midnight can still be prior ET calendar day
    assert date_et("2026-08-22T00:00:02+00:00") == "2026-08-21"
    assert date_et(None) == ""
    print("ok date_et")


def test_next_4h_close_et_labeled():
    now = datetime(2026, 8, 23, 17, 30, tzinfo=timezone.utc)  # 1:30 PM ET
    nxt = next_4h_close_et(now)
    assert "UTC" not in nxt
    assert nxt == "2026-08-23 4:00 PM ET"  # 20:00 UTC
    print("ok next 4h close ET")


def test_alert_ts_is_et():
    from scripts.trade_alert_poll import format_alert

    text = format_alert(
        {
            "action": "LIVE_BUY",
            "mode": "LIVE",
            "symbol": "PUMP",
            "qty": 1,
            "price": 1,
            "ts": "2026-08-23T20:00:31+00:00",
        }
    )
    assert "UTC" not in text
    assert "+00:00" not in text
    assert "2026-08-23 4:00 PM ET" in text
    print("ok alert ET")


def test_paper_status_display_ts():
    from scripts import paper_status

    s = paper_status.display_ts("2026-08-21T22:50:37+00:00")
    assert s == "2026-08-21 6:50 PM ET"
    assert "UTC" not in paper_status.NO_DAY_MARK
    print("ok paper_status display_ts")


if __name__ == "__main__":
    test_format_et_summer_iso()
    test_format_et_winter_iso()
    test_format_et_z_and_naive()
    test_format_et_none()
    test_date_et_first_live_fill()
    test_next_4h_close_et_labeled()
    test_alert_ts_is_et()
    test_paper_status_display_ts()
    print("ALL DISPLAY TIME PASS")
