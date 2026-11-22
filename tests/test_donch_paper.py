#!/usr/bin/env python3
"""Isolated donch paper soak: daily clock, FamilyEngine, path firewall."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DAY0 = int(datetime(2026, 9, 5, tzinfo=timezone.utc).timestamp() * 1000)
DAY1 = DAY0 + 24 * 3600 * 1000
NOW_MID = DAY0 + 12 * 3600 * 1000  # forming day0
NOW_CLOSE = DAY1  # day0 just closed


def _feed(td: str, symbols=None):
    from strategies.daily_data_feed import DailyDataFeed

    emitted: list[str] = []

    def on_bar(sym, bar):
        emitted.append(sym)

    feed = DailyDataFeed(
        symbols or ["AAA", "ZZZ"],
        on_bar,
        health_path=os.path.join(td, "feed_health_donch.json"),
    )
    return feed, emitted


def test_daily_early_next_day_does_not_emit():
    from strategies.daily_data_feed import bucket_ms_from_bar_ts

    with tempfile.TemporaryDirectory() as td:
        feed, emitted = _feed(td, ["WLD"])
        feed._ingest_5m(
            "WLD",
            [
                [DAY0, 1.0, 1.0, 1.0, 1.0, 1],
                [DAY1, 1.1, 1.1, 1.1, 1.1, 1],
            ],
            now_ms=NOW_MID,
        )
        feed._flush_pending()
        assert emitted == []
        assert feed._cur["WLD"]["bucket"] == DAY0
        feed._maybe_emit_closed("WLD", now_ms=NOW_CLOSE)
        feed._flush_pending()
        assert emitted == ["WLD"]
        assert feed._last_bar_bucket == DAY0
        print("ok daily early-hold then time-close")


def test_seed_stamps_closed_bucket_end_not_wall_clock():
    with tempfile.TemporaryDirectory() as td:
        feed, emitted = _feed(td, ["BTC", "ETH"])
        closed = DAY0  # 2026-09-05 00:00 UTC; closes at DAY1
        bar = {
            "ts": closed * 1_000_000,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
        }
        now_ms = DAY1 + 15 * 3600 * 1000  # 15:00 UTC the day after close
        feed.init_emitted_from_seed({"ETH": [bar], "BTC": [bar]}, now_ms=now_ms)
        assert emitted == []
        assert feed._bars_emitted == 0
        assert feed._last_bar_bucket == closed
        assert isinstance(feed._last_bar_ts, str)
        stamped = datetime.fromisoformat(feed._last_bar_ts)
        assert stamped == datetime(2026, 9, 6, tzinfo=timezone.utc), feed._last_bar_ts
        # A later live emit must not be overwritten by another seed note.
        feed._bars_emitted = 1
        feed._last_bar_ts = "live-emit"
        feed._note_closed_bucket("BTC", closed)
        assert feed._last_bar_ts == "live-emit"
        print("ok seed stamps closed bucket end")


def test_same_day_flush_is_alpha():
    with tempfile.TemporaryDirectory() as td:
        feed, emitted = _feed(td, ["ZZZ", "AAA"])
        for sym, px in (("ZZZ", 2.0), ("AAA", 1.0)):
            feed._ingest_5m(sym, [[DAY0, px, px, px, px, 1]], now_ms=NOW_CLOSE)
            feed._maybe_emit_closed(sym, now_ms=NOW_CLOSE)
        feed._flush_pending()
        assert emitted == ["AAA", "ZZZ"], emitted
        print("ok same-day A-Z flush")


def test_health_path_required():
    from strategies.daily_data_feed import DailyDataFeed

    try:
        DailyDataFeed(["BTC"], lambda s, b: None, health_path="")
    except ValueError:
        print("ok health_path required")
        return
    raise AssertionError("expected ValueError")


def test_live_node_does_not_import_donch():
    text = (ROOT / "stratforge" / "strategies" / "live_node.py").read_text()
    assert "research_families" not in text
    assert "paper_family_node" not in text
    assert "daily_data_feed" not in text
    assert "execution_log_donch" not in text
    print("ok live_node isolation")


def test_ssot_paper_unit_untouched():
    unit = (ROOT / "deploy" / "stratforge-paper.service.example").read_text()
    assert "live_paper.json" in unit
    assert "donch" not in unit.lower()
    assert "paper_family" not in unit
    assert "-m stratforge.live" in unit
    paper_cfg = (ROOT / "config" / "live_paper.json").read_text()
    assert "trend_rotation_25_max8_rotate" in paper_cfg
    assert "donch" not in paper_cfg
    print("ok SSOT paper unit/config")


def test_family_bar_buy_and_exit(tmp_path: Path):
    from strategies.bar_apply import apply_family_bar
    from strategies.paper_executor import PaperExecutor
    from strategies.research_families import FamilyEngine

    journal = tmp_path / "execution_log_donch.jsonl"
    eng = FamilyEngine(
        {
            "family": "donch_d20_10",
            "bar_minutes": 1440,
            "entry_n": 3,
            "exit_n": 2,
            "max_concurrent": 8,
            "max_alloc_pct": 0.25,
            "rotate_when_full": True,
            "rotate_min_hold_bars": 1,
        },
        ["AAA"],
    )
    ex = PaperExecutor(
        start_usd=500.0,
        max_alloc_pct=0.25,
        journal_path=str(journal),
        fee_rate=0.001,
        write_opening=True,
    )
    ts0 = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    day = 24 * 3600 * 1_000_000_000
    # Warm 3 bars (need entry_n+1 highs in deque; update appends current then looks at prior n)
    for i, px in enumerate([10.0, 10.5, 11.0, 11.2]):
        bar = {"ts": ts0 + i * day, "open": px, "high": px, "low": px, "close": px, "volume": 1}
        apply_family_bar(eng, ex, "AAA", bar, apply_enabled=False)
    # Breakout above prior 3-day high (11.2)
    brk = {"ts": ts0 + 4 * day, "open": 12.0, "high": 12.5, "low": 12.0, "close": 12.5, "volume": 1}
    acts = apply_family_bar(eng, ex, "AAA", brk, apply_enabled=True)
    assert any(a["type"] == "BUY" for a in acts), acts
    assert "AAA" in ex.positions
    # Exit below prior 2-day low
    xt = {"ts": ts0 + 5 * day, "open": 8.0, "high": 8.0, "low": 7.0, "close": 7.0, "volume": 1}
    acts2 = apply_family_bar(eng, ex, "AAA", xt, apply_enabled=True)
    assert any(a["type"] == "CLOSE" for a in acts2), acts2
    assert "AAA" not in ex.positions
    text = journal.read_text()
    assert "PAPER_BUY" in text
    assert "execution_log.jsonl" not in str(journal)
    print("ok family buy/exit on isolated journal")


def test_feed_health_ignores_missing_donch(tmp_path: Path):
    import json
    from datetime import datetime, timezone

    from scripts.ops_watchdog import collect_watchdog_messages

    data = tmp_path / "data"
    data.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    (data / "feed_health.json").write_text(
        json.dumps({"ts": now, "cycle": 5, "fetch_ok": 10, "bars_emitted": 1})
    )
    msgs = collect_watchdog_messages(tmp_path, [])
    assert msgs == []
    assert not (data / "feed_health_donch.json").exists()


def test_donch_status_paths_isolated():
    from scripts import paper_status_donch as d

    assert d.JOURNAL.name == "execution_log_donch.jsonl"
    assert d.NODE_HEALTH.name == "node_health_donch.json"
    assert d.FEED_HEALTH.name == "feed_health_donch.json"
    print("ok donch status paths")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as P

    test_daily_early_next_day_does_not_emit()
    test_seed_stamps_closed_bucket_end_not_wall_clock()
    test_same_day_flush_is_alpha()
    test_health_path_required()
    test_live_node_does_not_import_donch()
    test_ssot_paper_unit_untouched()
    with tempfile.TemporaryDirectory() as td:
        test_family_bar_buy_and_exit(P(td))
        test_feed_health_ignores_missing_donch(P(td))
    test_donch_status_paths_isolated()
    print("all ok")
