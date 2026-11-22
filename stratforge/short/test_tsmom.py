#!/usr/bin/env python3
"""Unit tests for tsmom12_weekly. Run: .venv/bin/python short/test_tsmom.py"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from short.tsmom import Tsmom12Engine, TsmomConfig, _week_id  # noqa: E402


def _bar(ts: int, c: float) -> dict:
    return {"ts": ts, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1.0}


def _week_ts(week_i: int, hour: int = 0) -> int:
    # 2020-01-06 is a Monday; step weeks from there
    start = datetime(2020, 1, 6, hour, tzinfo=timezone.utc) + timedelta(weeks=week_i)
    return int(start.timestamp() * 1e9)


def test_week_id_stable() -> None:
    a = _week_ts(0, 0)
    b = _week_ts(0, 20)
    c = _week_ts(1, 0)
    assert _week_id(a) == _week_id(b)
    assert _week_id(a) != _week_id(c)


def test_no_short_until_lookback_ready() -> None:
    cfg = TsmomConfig(lookback_weeks=4)
    eng = Tsmom12Engine(cfg, ["BTC"])
    px = 100.0
    shorts = 0
    for w in range(4):
        for h in (0, 4, 8, 12, 16, 20):
            ts = _week_ts(w, h)
            px *= 0.98
            acts = eng.on_bar("BTC", _bar(ts, px), emit=True, positions={})
            shorts += sum(1 for a in acts if a["type"] == "SHORT")
    assert shorts == 0
    assert eng.regime_ready is False


def test_short_when_12m_negative_cover_when_flips() -> None:
    cfg = TsmomConfig(lookback_weeks=4)
    eng = Tsmom12Engine(cfg, ["BTC"])
    positions: dict = {}
    px = 100.0
    shorts = covers = 0
    # 4 weeks up to seed, then dump until 12m < 0, then recover
    for w in range(4):
        for h in (0, 4, 8, 12, 16, 20):
            px *= 1.02
            eng.on_bar("BTC", _bar(_week_ts(w, h), px), emit=True, positions=positions)
    for w in range(4, 12):
        for h in (0, 4, 8, 12, 16, 20):
            px *= 0.94
            acts = eng.on_bar("BTC", _bar(_week_ts(w, h), px), emit=True, positions=positions)
            for a in acts:
                if a["type"] == "SHORT":
                    shorts += 1
                    positions["BTC"] = {"side": "SHORT", "entry": a["price"], "ts": w}
                elif a["type"] == "COVER":
                    covers += 1
                    positions.pop("BTC", None)
    assert shorts >= 1
    assert eng.regime_bear is True
    for w in range(12, 24):
        for h in (0, 4, 8, 12, 16, 20):
            px *= 1.08
            acts = eng.on_bar("BTC", _bar(_week_ts(w, h), px), emit=True, positions=positions)
            for a in acts:
                if a["type"] == "COVER":
                    covers += 1
                    positions.pop("BTC", None)
    assert covers >= 1
    assert eng.regime_bear is False


def test_eth_blocked_if_only_eth_is_bear() -> None:
    cfg = TsmomConfig(lookback_weeks=4, eth_requires_own_bear=True)
    eng = Tsmom12Engine(cfg, ["BTC", "ETH"])
    # BTC grind up, ETH dump
    btc, eth = 100.0, 100.0
    eth_shorts = 0
    for w in range(10):
        for h in (0, 12):
            btc *= 1.03
            eth *= 0.94
            ts = _week_ts(w, h)
            eng.on_bar("BTC", _bar(ts, btc), emit=True, positions={})
            acts = eng.on_bar("ETH", _bar(ts + 1, eth), emit=True, positions={})
            eth_shorts += sum(1 for a in acts if a["type"] == "SHORT")
    assert eng.regime_bear is False
    assert eth_shorts == 0


def test_isolation() -> None:
    import sys as _sys

    banned = {
        "strategies.signal_engine",
        "strategies.paper_executor",
        "strategies.research_replay",
        "strategies.live_node",
    }
    import short.tsmom  # noqa: F401

    hit = banned.intersection(_sys.modules)
    assert not hit, f"tsmom imported long modules: {hit}"


def main() -> int:
    tests = [
        test_week_id_stable,
        test_no_short_until_lookback_ready,
        test_short_when_12m_negative_cover_when_flips,
        test_eth_blocked_if_only_eth_is_bear,
        test_isolation,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
