#!/usr/bin/env python3
"""4h fold must not emit (or stamp _last_emitted) before official bucket close.

REST-only cases stay on the production feed. Native LiveNode cases live under
archive/thin_host_nautilus/tests/.
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
BKT_2000 = BKT_0000 - 4 * 3600 * 1000  # prior closed 20:00
BKT_0400 = BKT_0000 + 4 * 3600 * 1000
NOW_0033 = BKT_0000 + 33 * 60 * 1000  # 00:33 — forming 00:00 bucket
NOW_0334 = BKT_0000 + int(3.57 * 3600 * 1000)  # ~03:34
NOW_0400 = BKT_0400


def _bar(bucket_ms: int, close: float) -> dict:
    from strategies.live_data_feed import bar_ts_ns_from_bucket_ms

    return {
        "ts": bar_ts_ns_from_bucket_ms(bucket_ms),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
    }


def _feed(cls, td: str):
    emitted: list[tuple[str, int, float]] = []

    def on_4h(sym, bar):
        emitted.append((sym, int(bar["ts"]), float(bar["close"])))

    feed = cls(
        ["WLD"],
        on_4h_bar=on_4h,
        health_path=os.path.join(td, "health.json"),
    )
    return feed, emitted


def test_rest_early_next_bucket_does_not_emit():
    from strategies.live_data_feed import LiveDataFeed, bucket_ms_from_bar_ts

    with tempfile.TemporaryDirectory() as td:
        feed, emitted = _feed(LiveDataFeed, td)
        feed._ingest_5m(
            "WLD",
            [
                [BKT_0000, 0.36, 0.36, 0.36, 0.36, 1],
                [BKT_0400, 0.366, 0.366, 0.366, 0.366, 1],
            ],
            now_ms=NOW_0334,
        )
        assert emitted == []
        assert feed._cur["WLD"]["bucket"] == BKT_0000
        feed._maybe_emit_closed("WLD", now_ms=NOW_0400)
        assert len(emitted) == 1
        assert bucket_ms_from_bar_ts(emitted[0][1]) == BKT_0000
        print("ok rest early-hold then time-close")


def test_trim_unclosed_seed_drops_forming_tail():
    from strategies.live_data_feed import trim_unclosed_seed_bars, bucket_ms_from_bar_ts

    bars = [_bar(BKT_2000, 1.0), _bar(BKT_0000, 1.1)]
    closed = trim_unclosed_seed_bars(bars, now_ms=NOW_0033)
    assert [bucket_ms_from_bar_ts(b["ts"]) for b in closed] == [BKT_2000]
    still = trim_unclosed_seed_bars(bars, now_ms=NOW_0400)
    assert [bucket_ms_from_bar_ts(b["ts"]) for b in still] == [BKT_2000, BKT_0000]
    print("ok trim forming seed tail")


def test_forming_seed_tail_does_not_block_first_close():
    """Restart mid-bucket must still emit the first official 4h close.

    Incident 2026-08-23: seed resample included the still-forming 00:00 bar,
    _last_emitted stamped that bucket, 04:00 time-close was silently dropped.
    """
    from strategies.live_data_feed import LiveDataFeed, bucket_ms_from_bar_ts

    with tempfile.TemporaryDirectory() as td:
        feed, emitted = _feed(LiveDataFeed, td)
        seed = {"WLD": [_bar(BKT_2000, 1.0), _bar(BKT_0000, 1.1)]}
        feed.init_emitted_from_seed(seed, now_ms=NOW_0033)
        assert feed._last_emitted["WLD"] == BKT_2000

        feed._ingest_5m(
            "WLD",
            [[BKT_0000 + 5 * 60 * 1000, 1.1, 1.12, 1.09, 1.11, 1]],
            now_ms=NOW_0033,
        )
        feed._maybe_emit_closed("WLD", now_ms=NOW_0033)
        assert emitted == []

        feed._maybe_emit_closed("WLD", now_ms=NOW_0400)
        assert len(emitted) == 1, emitted
        assert bucket_ms_from_bar_ts(emitted[0][1]) == BKT_0000
        print("ok forming seed tail does not block first close")


def test_emit_logs_dedup_skip_once():
    from strategies.live_data_feed import LiveDataFeed

    with tempfile.TemporaryDirectory() as td:
        feed, emitted = _feed(LiveDataFeed, td)
        feed._last_emitted["WLD"] = BKT_0000
        ok = feed._emit("WLD", _bar(BKT_0000, 1.1), reason="time-close")
        assert ok is False
        assert emitted == []
        print("ok dedup skip does not emit")


if __name__ == "__main__":
    test_rest_early_next_bucket_does_not_emit()
    test_trim_unclosed_seed_drops_forming_tail()
    test_forming_seed_tail_does_not_block_first_close()
    test_emit_logs_dedup_skip_once()
    print("ALL FEED EMIT GATE TESTS PASS")
