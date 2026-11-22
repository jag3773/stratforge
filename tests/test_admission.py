#!/usr/bin/env python3
"""Unit tests for admission MVP (no network required except optional skip)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_universe_loader():
    from strategies.universe import load_live_universe, LIVE_PATH

    u = load_live_universe()
    assert len(u) >= 40
    assert "BTC" in u and "ETH" in u
    assert LIVE_PATH.is_file()
    # live_node must use same list
    from strategies.live_node import UNIVERSE

    assert UNIVERSE == u
    print("ok universe")


def test_store_roundtrip(tmp_path=None):
    import scripts.admission.store as store

    # redirect paths into temp
    td = Path(tempfile.mkdtemp(prefix="admission_test_"))
    store.ADMISSION_DIR = td
    store.WATCHLIST = td / "watchlist.jsonl"
    store.ADMISSION_LOG = td / "admission_log.jsonl"
    store.CHANGELOG = td / "changelog.md"
    store.SCORECARDS = td / "scorecards"
    store.ensure_dirs()
    store.upsert_watch("FOO", status="discovered", source="test")
    store.upsert_watch("FOO", status="qa_ok")
    wl = store.load_watchlist()
    assert wl["FOO"]["status"] == "qa_ok"
    p = store.write_scorecard("FOO", {"stage": "test", "ok": True})
    assert p.is_file()
    print("ok store", td)


def test_research_replay_btc():
    from scripts.admission.research_engine import run_portfolio_replay
    from strategies.research_replay import RAW_DIR

    # CI has no candle warehouse. The desk still asserts bars when the file is present.
    path = RAW_DIR / "BTC_USD_5m.parquet"
    if not path.is_file():
        pytest.skip(f"warehouse candle file missing: {path.name}")

    # short window for speed
    r = run_portfolio_replay(["BTC"], start="2025-01-01", end="2025-06-01")
    assert r.bars > 50
    assert r.end_equity > 0
    print(f"ok replay BTC bars={r.bars} trades={r.trades} ret={r.net_return_pct:.3%}")


def test_promote_demote_file(tmp_path_factory=None):
    import scripts.admission.store as store
    from strategies import universe as uni

    td = Path(tempfile.mkdtemp(prefix="admission_univ_"))
    live = td / "universe_live.json"
    live.write_text(
        json.dumps({"symbols": ["BTC", "ETH", "SOL"], "updated_by": "test"}),
        encoding="utf-8",
    )
    store.UNIVERSE_LIVE = live
    store.ADMISSION_DIR = td / "admission"
    store.WATCHLIST = store.ADMISSION_DIR / "watchlist.jsonl"
    store.ADMISSION_LOG = store.ADMISSION_DIR / "admission_log.jsonl"
    store.CHANGELOG = store.ADMISSION_DIR / "changelog.md"
    store.SCORECARDS = store.ADMISSION_DIR / "scorecards"
    store.ensure_dirs()

    store.save_live_symbols(["BTC", "ETH", "SOL", "AAA"], updated_by="test", note="promote AAA")
    syms = store.load_live_symbols()
    assert "AAA" in syms and len(syms) == 4
    store.save_live_symbols(["BTC", "ETH", "SOL"], updated_by="test", note="demote AAA")
    assert "AAA" not in store.load_live_symbols()
    print("ok promote/demote file")


if __name__ == "__main__":
    test_universe_loader()
    test_store_roundtrip()
    test_research_replay_btc()
    test_promote_demote_file()
    print("ALL ADMISSION TESTS PASS")
