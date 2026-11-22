#!/usr/bin/env python3
"""Unit tests for the isolated short stack. Run: .venv/bin/python short/test_short.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from short.engine import BearConfig, BearRegimeEngine
from short.indicators import ATR, EMA
from short.ledger import ShortLedger


def _bar(ts: int, c: float, h: float | None = None, l: float | None = None) -> dict:
    return {
        "ts": ts,
        "open": c,
        "high": h if h is not None else c * 1.001,
        "low": l if l is not None else c * 0.999,
        "close": c,
        "volume": 1.0,
    }


def test_ema_not_ready_until_period() -> None:
    e = EMA(5)
    for px in (10, 11, 12, 13):
        e.handle_bar(px)
        assert e.initialized is False
    e.handle_bar(14)
    assert e.initialized is True
    assert abs(e.value - 12.0) < 1e-9


def test_ledger_short_profit_and_fee() -> None:
    led = ShortLedger(start_usd=10_000, fee_rate=0.001, funding_per_8h=0.0, max_leverage=1.0)
    led.update_price("BTC", 100.0, 1)
    rec = led.short("BTC", 100.0, 1, target_notional=5_000, reason="t")
    assert rec is not None
    assert rec["qty"] == 50.0
    assert abs(led.cash - (10_000 - 5.0)) < 1e-6
    led.update_price("BTC", 90.0, 2)
    # +10 * 50 = +500 unrealized
    assert abs(led.unrealized() - 500.0) < 1e-6
    cover = led.cover("BTC", 90.0, 2, reason="t")
    assert cover is not None
    assert abs(cover["pnl"] - 500.0) < 1e-6
    assert led.wins == 1
    assert led.lots == {}


def test_no_short_in_uptrend() -> None:
    cfg = BearConfig(slow_ema_period=5, atr_period=3, atr_stop_mult=2.0, cooldown_bars=0)
    eng = BearRegimeEngine(cfg, ["BTC"])
    px = 100.0
    actions_all = []
    for i in range(20):
        px *= 1.02
        acts = eng.on_bar("BTC", _bar(i, px), emit=True, positions={})
        actions_all.extend(acts)
    assert not any(a["type"] == "SHORT" for a in actions_all)
    assert eng.regime_bear is False


def test_short_in_downtrend_then_cover_on_flip() -> None:
    cfg = BearConfig(slow_ema_period=5, atr_period=3, atr_stop_mult=50.0, cooldown_bars=0)
    eng = BearRegimeEngine(cfg, ["BTC"])
    positions: dict = {}
    shorts = covers = 0
    px = 100.0
    # climb to seed EMA high
    for i in range(8):
        px *= 1.01
        eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
    # dump
    for i in range(8, 24):
        px *= 0.97
        acts = eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
        for a in acts:
            if a["type"] == "SHORT":
                shorts += 1
                positions["BTC"] = {"side": "SHORT", "entry": a["price"], "ts": i}
            elif a["type"] == "COVER":
                covers += 1
                positions.pop("BTC", None)
    assert shorts >= 1
    assert eng.regime_bear is True
    # recover above EMA
    for i in range(24, 50):
        px *= 1.04
        acts = eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
        for a in acts:
            if a["type"] == "COVER":
                covers += 1
                positions.pop("BTC", None)
    assert covers >= 1


def test_atr_stop_covers_squeeze() -> None:
    cfg = BearConfig(slow_ema_period=5, atr_period=3, atr_stop_mult=0.2, cooldown_bars=0)
    eng = BearRegimeEngine(cfg, ["BTC"])
    positions: dict = {}
    px = 100.0
    for i in range(8):
        px *= 1.01
        eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
    # one breakdown bar → short, then a bounce that clears 0.5 ATR but stays under EMA
    px *= 0.97
    acts = eng.on_bar("BTC", _bar(8, px), emit=True, positions=positions)
    assert any(a["type"] == "SHORT" for a in acts)
    entry = next(a["price"] for a in acts if a["type"] == "SHORT")
    positions["BTC"] = {"side": "SHORT", "entry": entry, "ts": 8}
    st = eng.states["BTC"]
    stop = entry + 0.2 * float(st.atr.value)
    bounce = stop + 0.01
    assert bounce < st.ema.value
    acts = eng.on_bar("BTC", _bar(9, bounce, h=bounce, l=entry), emit=True, positions=positions)
    assert any(a["type"] == "COVER" and a["reason"] == "atr_stop" for a in acts)


def test_isolation_does_not_import_long_stack() -> None:
    import sys as _sys

    banned = {
        "strategies.signal_engine",
        "strategies.paper_executor",
        "strategies.research_replay",
        "strategies.live_node",
    }
    # import our stack fresh-ish
    import short.engine  # noqa: F401
    import short.ledger  # noqa: F401
    import short.replay  # noqa: F401

    hit = banned.intersection(_sys.modules)
    assert not hit, f"short stack imported long modules: {hit}"


def test_ema_falling_blocks_pullback() -> None:
    cfg = BearConfig(
        slow_ema_period=5,
        atr_period=3,
        atr_stop_mult=50.0,
        cooldown_bars=0,
        require_ema_falling=True,
    )
    eng = BearRegimeEngine(cfg, ["BTC"])
    px = 100.0
    for i in range(12):
        px *= 1.03
        eng.on_bar("BTC", _bar(i, px), emit=True, positions={})
    assert eng.regime_bear is False
    assert eng.ema_falling is False
    # one dip below a still-rising EMA must not short
    px *= 0.96
    acts = eng.on_bar("BTC", _bar(12, px), emit=True, positions={})
    assert not any(a["type"] == "SHORT" for a in acts)


def test_persist_requires_consecutive_closes() -> None:
    cfg = BearConfig(
        slow_ema_period=5,
        atr_period=3,
        atr_stop_mult=50.0,
        cooldown_bars=0,
        entry_persist_bars=3,
    )
    eng = BearRegimeEngine(cfg, ["BTC"])
    positions: dict = {}
    px = 100.0
    for i in range(8):
        px *= 1.01
        eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
    # Drive below EMA; first two persist bars must not short.
    shorts = []
    i = 8
    while eng.bear_persist < 1 and i < 40:
        px *= 0.96
        acts = eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
        shorts.extend(a for a in acts if a["type"] == "SHORT")
        i += 1
    assert eng.bear_persist >= 1
    assert not shorts
    # persist == 1 now; one more bar → 2, still blocked
    px *= 0.96
    acts = eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
    i += 1
    assert eng.bear_persist == 2
    assert not any(a["type"] == "SHORT" for a in acts)
    px *= 0.96
    acts = eng.on_bar("BTC", _bar(i, px), emit=True, positions=positions)
    assert eng.bear_persist >= 3
    assert any(a["type"] == "SHORT" for a in acts)


def test_daily_lock_blocks_4h_dip_in_daily_bull() -> None:
    from datetime import datetime, timedelta, timezone

    cfg = BearConfig(
        slow_ema_period=5,
        atr_period=3,
        atr_stop_mult=50.0,
        cooldown_bars=0,
        daily_ema_period=3,
    )
    eng = BearRegimeEngine(cfg, ["BTC"])
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    px = 100.0
    n = 0
    # ~5 days of grind up so daily EMA is up and initialized
    for d in range(5):
        for h in (0, 4, 8, 12, 16, 20):
            ts = int((start + timedelta(days=d, hours=h)).timestamp() * 1e9)
            px *= 1.01
            eng.on_bar("BTC", _bar(ts, px), emit=True, positions={})
            n += 1
    assert eng.daily_ready is True
    assert eng.daily_bear is False
    # 4h dump that crosses 4h EMA but daily is still bull
    ts = int((start + timedelta(days=5, hours=0)).timestamp() * 1e9)
    px *= 0.90
    acts = eng.on_bar("BTC", _bar(ts, px), emit=True, positions={})
    assert not any(a["type"] == "SHORT" for a in acts)


def test_integer_contracts_floor() -> None:
    led = ShortLedger(
        start_usd=10_000,
        fee_rate=0.0,
        funding_per_8h=0.0,
        integer_contracts=True,
        max_leverage=1.0,
    )
    # BTC $100k → 1 BIP = $1k; 50% of 10k = $5k → 5 contracts → 0.05 BTC
    rec = led.short("BTC", 100_000.0, 1, target_notional=5_000, reason="t")
    assert rec is not None
    assert abs(rec["qty"] - 0.05) < 1e-12


def main() -> int:
    tests = [
        test_ema_not_ready_until_period,
        test_ledger_short_profit_and_fee,
        test_no_short_in_uptrend,
        test_short_in_downtrend_then_cover_on_flip,
        test_atr_stop_covers_squeeze,
        test_isolation_does_not_import_long_stack,
        test_ema_falling_blocks_pullback,
        test_persist_requires_consecutive_closes,
        test_daily_lock_blocks_4h_dip_in_daily_bull,
        test_integer_contracts_floor,
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
