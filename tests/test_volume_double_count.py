"""Regression tests for #11: cumulative volume double-counting.

_ingest_5m() re-fetches the same 100/300 candles every poll cycle.
Before the fix, calling _ingest_5m() twice with the same rows doubled
(and tripled, …) the volume on the open bucket.

Each test must FAIL without the fix (i.e., without the last_5m_ts guard).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 00:00 UTC 17 Aug 2026
BKT_0000 = 1_786_924_800_000
NOW_0200 = BKT_0000 + 2 * 3600 * 1000  # mid-bucket, not yet closed

# 5m candle timestamps inside the BKT_0000 4h bucket
_5M_A = BKT_0000
_5M_B = BKT_0000 + 5 * 60 * 1000
_5M_C = BKT_0000 + 10 * 60 * 1000


def _feed_live(td: str):
    from strategies.live_data_feed import LiveDataFeed

    emitted: list = []

    def on_4h(sym, bar):
        emitted.append((sym, bar.copy()))

    feed = LiveDataFeed(
        ["BTC"],
        on_4h_bar=on_4h,
        health_path=os.path.join(td, "health.json"),
    )
    return feed, emitted


def _feed_daily(td: str):
    from strategies.daily_data_feed import DailyDataFeed

    emitted: list = []

    def on_bar(sym, bar):
        emitted.append((sym, bar.copy()))

    feed = DailyDataFeed(
        ["BTC"],
        on_bar=on_bar,
        health_path=os.path.join(td, "health_donch.json"),
    )
    return feed, emitted


# ---------------------------------------------------------------------------
# LiveDataFeed tests
# ---------------------------------------------------------------------------


def test_live_no_double_volume_on_repeated_ingest():
    """Volume must not grow when the same 5m rows are re-ingested (#11)."""
    with tempfile.TemporaryDirectory() as td:
        feed, _ = _feed_live(td)
        rows = [
            [_5M_A, 100.0, 101.0, 99.0, 100.5, 10.0],
            [_5M_B, 100.5, 102.0, 100.0, 101.0, 20.0],
        ]
        feed._ingest_5m("BTC", rows, now_ms=NOW_0200)
        vol_after_first = feed._cur["BTC"]["bar"]["volume"]

        # Same rows — simulates second poll cycle
        feed._ingest_5m("BTC", rows, now_ms=NOW_0200)
        vol_after_second = feed._cur["BTC"]["bar"]["volume"]

        assert vol_after_second == vol_after_first, (
            f"Volume doubled on second ingest: {vol_after_first} -> {vol_after_second}"
        )


def test_live_new_5m_candle_adds_volume():
    """A genuinely new 5m candle that arrives on the second poll MUST add volume."""
    with tempfile.TemporaryDirectory() as td:
        feed, _ = _feed_live(td)
        first_rows = [
            [_5M_A, 100.0, 101.0, 99.0, 100.5, 10.0],
            [_5M_B, 100.5, 102.0, 100.0, 101.0, 20.0],
        ]
        feed._ingest_5m("BTC", first_rows, now_ms=NOW_0200)
        vol_after_first = feed._cur["BTC"]["bar"]["volume"]

        # Second poll: same old rows + one new 5m candle
        second_rows = first_rows + [[_5M_C, 101.0, 103.0, 100.5, 102.0, 15.0]]
        feed._ingest_5m("BTC", second_rows, now_ms=NOW_0200)
        vol_after_second = feed._cur["BTC"]["bar"]["volume"]

        assert vol_after_second == vol_after_first + 15.0, (
            f"Expected {vol_after_first + 15.0}, got {vol_after_second}"
        )


def test_live_triple_ingest_no_double_volume():
    """Three identical poll cycles must not multiply volume."""
    with tempfile.TemporaryDirectory() as td:
        feed, _ = _feed_live(td)
        rows = [[_5M_A, 50.0, 51.0, 49.0, 50.5, 5.0]]
        feed._ingest_5m("BTC", rows, now_ms=NOW_0200)
        expected = feed._cur["BTC"]["bar"]["volume"]
        feed._ingest_5m("BTC", rows, now_ms=NOW_0200)
        feed._ingest_5m("BTC", rows, now_ms=NOW_0200)
        assert feed._cur["BTC"]["bar"]["volume"] == expected


# ---------------------------------------------------------------------------
# DailyDataFeed tests (same logic, 1d buckets)
# ---------------------------------------------------------------------------

DAY_MS = 24 * 3600 * 1000
BKT_DAY = (BKT_0000 // DAY_MS) * DAY_MS
NOW_DAY_MID = BKT_DAY + 12 * 3600 * 1000
_5M_DA = BKT_DAY
_5M_DB = BKT_DAY + 5 * 60 * 1000
_5M_DC = BKT_DAY + 10 * 60 * 1000


def test_daily_no_double_volume_on_repeated_ingest():
    """DailyDataFeed: same rows re-ingested must not accumulate volume (#11)."""
    with tempfile.TemporaryDirectory() as td:
        feed, _ = _feed_daily(td)
        rows = [
            [_5M_DA, 200.0, 201.0, 199.0, 200.5, 50.0],
            [_5M_DB, 200.5, 202.0, 200.0, 201.0, 60.0],
        ]
        feed._ingest_5m("BTC", rows, now_ms=NOW_DAY_MID)
        vol_first = feed._cur["BTC"]["bar"]["volume"]

        feed._ingest_5m("BTC", rows, now_ms=NOW_DAY_MID)
        vol_second = feed._cur["BTC"]["bar"]["volume"]

        assert vol_second == vol_first, (
            f"Daily volume doubled: {vol_first} -> {vol_second}"
        )


def test_daily_new_5m_candle_adds_volume():
    """DailyDataFeed: a genuinely new candle on second poll must add volume."""
    with tempfile.TemporaryDirectory() as td:
        feed, _ = _feed_daily(td)
        first_rows = [
            [_5M_DA, 200.0, 201.0, 199.0, 200.5, 50.0],
            [_5M_DB, 200.5, 202.0, 200.0, 201.0, 60.0],
        ]
        feed._ingest_5m("BTC", first_rows, now_ms=NOW_DAY_MID)
        vol_first = feed._cur["BTC"]["bar"]["volume"]

        second_rows = first_rows + [[_5M_DC, 201.0, 203.0, 200.5, 202.0, 25.0]]
        feed._ingest_5m("BTC", second_rows, now_ms=NOW_DAY_MID)
        vol_second = feed._cur["BTC"]["bar"]["volume"]

        assert vol_second == vol_first + 25.0, (
            f"Expected {vol_first + 25.0}, got {vol_second}"
        )
