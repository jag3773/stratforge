#!/usr/bin/env python3
"""Same-bar entry ranking by raw breakout % (research path)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.signal_engine import (  # noqa: E402
    SignalConfig,
    SignalEngine,
    breakout_atr,
    breakout_pct,
    rank_entry_actions,
)


def test_breakout_pct_formula():
    assert abs(breakout_pct(110.0, 100.0) - 0.10) < 1e-12
    assert abs(breakout_pct(101.0, 100.0) - 0.01) < 1e-12
    assert breakout_pct(100.0, 0.0) is None
    print("ok breakout_pct formula")


def test_rank_entry_actions_buys_desc_closes_first():
    acts = [
        {"type": "BUY", "symbol": "AAA", "score": 0.01, "reason": "breakout_long"},
        {"type": "CLOSE", "symbol": "OLD", "reason": "slow_ema_exit"},
        {"type": "BUY", "symbol": "ZZZ", "score": 0.10, "reason": "breakout_long"},
    ]
    ranked = rank_entry_actions(acts)
    assert ranked[0]["type"] == "CLOSE"
    buys = [a for a in ranked if a["type"] == "BUY"]
    assert [a["symbol"] for a in buys] == ["ZZZ", "AAA"]
    print("ok rank_entry_actions")


def _bar(ts: int, close: float, high: float | None = None) -> dict:
    h = close if high is None else high
    return {"ts": ts, "open": close, "high": h, "low": close * 0.99, "close": close, "volume": 1.0}


def _tiny_engine(symbols: list[str], max_concurrent: int = 1) -> SignalEngine:
    cfg = SignalConfig(
        mode="breakout",
        channel_period=5,
        fast_ema_period=3,
        slow_ema_period=3,
        adx_period=3,
        atr_period=3,
        rsi_period=3,
        atr_stop_mult=0.0,
        cooldown_bars=0,
        use_adx_filter=False,
        trend_filter=True,
        long_only=True,
        exit_mode="slow_ema",
        max_concurrent=max_concurrent,
        rotate_when_full=False,
        rotate_min_hold_bars=14,
    )
    return SignalEngine(cfg, symbols)


def test_same_ts_max1_rank_picks_larger_breakout():
    """AAA +1% then ZZZ +10% at same ts; rank_entries=breakout_pct takes ZZZ."""
    from strategies.research_replay import apply_bar_group

    eng = _tiny_engine(["AAA", "ZZZ"], max_concurrent=1)
    # Rising channel so last bar can break out. Leave headroom under 100.
    for i in range(8):
        px = 90.0 + i
        ts = i * 1_000_000_000
        for sym in ("AAA", "ZZZ"):
            eng.on_bar(sym, _bar(ts, px), emit=False)

    ts = 99 * 1_000_000_000
    group = [
        ("AAA", _bar(ts, 101.0, high=101.0)),  # +~1% vs ~100 channel
        ("ZZZ", _bar(ts, 110.0, high=110.0)),  # +~10%
    ]
    positions: dict = {}
    applied = apply_bar_group(
        eng,
        group,
        positions,
        rank_entries="breakout_pct",
        apply_fn=None,
    )
    buys = [a for a in applied if a.get("type") == "BUY"]
    assert len(buys) == 1, buys
    assert buys[0]["symbol"] == "ZZZ", buys
    assert float(buys[0]["score"]) > 0.05
    print("ok same-ts rank prefers ZZZ")


def test_same_ts_without_rank_keeps_stream_order():
    from strategies.research_replay import apply_bar_group

    eng = _tiny_engine(["AAA", "ZZZ"], max_concurrent=1)
    for i in range(8):
        px = 90.0 + i
        ts = i * 1_000_000_000
        for sym in ("AAA", "ZZZ"):
            eng.on_bar(sym, _bar(ts, px), emit=False)
    ts = 99 * 1_000_000_000
    group = [
        ("AAA", _bar(ts, 101.0, high=101.0)),
        ("ZZZ", _bar(ts, 110.0, high=110.0)),
    ]
    applied = apply_bar_group(eng, group, {}, rank_entries="", apply_fn=None)
    buys = [a for a in applied if a.get("type") == "BUY"]
    assert len(buys) == 1, buys
    assert buys[0]["symbol"] == "AAA", buys
    print("ok unranked keeps stream order AAA")


def test_breakout_atr_formula():
    assert abs(breakout_atr(110.0, 100.0, 5.0) - 2.0) < 1e-12
    assert abs(breakout_atr(101.0, 100.0, 0.2) - 5.0) < 1e-12
    assert breakout_atr(110.0, 100.0, 0.0) is None
    print("ok breakout_atr formula")


def test_same_ts_atr_rank_prefers_quiet_break_over_noisy_rip():
    """AAA +1% / tiny ATR vs ZZZ +10% / huge ATR → ATR-norm takes AAA."""
    from strategies.research_replay import apply_bar_group

    eng = _tiny_engine(["AAA", "ZZZ"], max_concurrent=1)
    for i in range(8):
        px = 90.0 + i
        ts = i * 1_000_000_000
        # Quiet tape on AAA, wild ranges on ZZZ so ATR diverges.
        eng.on_bar("AAA", _bar(ts, px, high=px + 0.05), emit=False)
        eng.on_bar("ZZZ", _bar(ts, px, high=px + 20.0), emit=False)

    ts = 99 * 1_000_000_000
    group = [
        ("AAA", _bar(ts, 101.0, high=101.05)),
        ("ZZZ", _bar(ts, 110.0, high=130.0)),
    ]
    applied = apply_bar_group(
        eng, group, {}, rank_entries="breakout_atr", apply_fn=None
    )
    buys = [a for a in applied if a.get("type") == "BUY"]
    assert len(buys) == 1, buys
    assert buys[0]["symbol"] == "AAA", buys
    print("ok same-ts ATR-norm prefers quiet AAA")


def test_same_ts_za_picks_zzz():
    from strategies.research_replay import apply_bar_group

    eng = _tiny_engine(["AAA", "ZZZ"], max_concurrent=1)
    for i in range(8):
        px = 90.0 + i
        ts = i * 1_000_000_000
        for sym in ("AAA", "ZZZ"):
            eng.on_bar(sym, _bar(ts, px), emit=False)
    ts = 99 * 1_000_000_000
    group = [
        ("AAA", _bar(ts, 101.0, high=101.0)),
        ("ZZZ", _bar(ts, 110.0, high=110.0)),
    ]
    applied = apply_bar_group(eng, group, {}, rank_entries="symbol_desc")
    buys = [a for a in applied if a.get("type") == "BUY"]
    assert len(buys) == 1, buys
    assert buys[0]["symbol"] == "ZZZ", buys
    print("ok Z-A prefers ZZZ")


def test_same_ts_least_picks_smaller_breakout():
    from strategies.research_replay import apply_bar_group

    eng = _tiny_engine(["AAA", "ZZZ"], max_concurrent=1)
    for i in range(8):
        px = 90.0 + i
        ts = i * 1_000_000_000
        for sym in ("AAA", "ZZZ"):
            eng.on_bar(sym, _bar(ts, px), emit=False)
    ts = 99 * 1_000_000_000
    group = [
        ("AAA", _bar(ts, 101.0, high=101.0)),
        ("ZZZ", _bar(ts, 110.0, high=110.0)),
    ]
    applied = apply_bar_group(eng, group, {}, rank_entries="breakout_least")
    buys = [a for a in applied if a.get("type") == "BUY"]
    assert len(buys) == 1, buys
    assert buys[0]["symbol"] == "AAA", buys
    print("ok least-extended prefers AAA")


def test_symbol_perm_is_deterministic_and_can_flip():
    from strategies.research_replay import apply_bar_group, symbol_perm_key

    def _run(seed: int) -> str:
        eng = _tiny_engine(["AAA", "ZZZ"], max_concurrent=1)
        for i in range(8):
            px = 90.0 + i
            ts = i * 1_000_000_000
            for sym in ("AAA", "ZZZ"):
                eng.on_bar(sym, _bar(ts, px), emit=False)
        ts = 99 * 1_000_000_000
        group = [
            ("AAA", _bar(ts, 101.0, high=101.0)),
            ("ZZZ", _bar(ts, 110.0, high=110.0)),
        ]
        applied = apply_bar_group(eng, group, {}, rank_entries=f"symbol_perm:{seed}")
        buys = [a for a in applied if a.get("type") == "BUY"]
        assert len(buys) == 1, buys
        return buys[0]["symbol"]

    first = _run(1)
    assert _run(1) == first
    expected = "AAA" if symbol_perm_key("AAA", 1) < symbol_perm_key("ZZZ", 1) else "ZZZ"
    assert first == expected, (first, expected)
    flip = None
    for seed in range(1, 200):
        winner = (
            "AAA"
            if symbol_perm_key("AAA", seed) < symbol_perm_key("ZZZ", seed)
            else "ZZZ"
        )
        if winner != first:
            flip = _run(seed)
            assert flip == winner
            break
    assert flip is not None and flip != first, (first, flip)
    print(f"ok symbol_perm seed1={first} flip={flip}")


def test_order_helpers_sort_stream():
    from strategies.research_replay import _invert_symbol, symbol_perm_key

    syms = ["AAA", "M", "ZZZ"]
    za = sorted(syms, key=_invert_symbol)
    assert za == ["ZZZ", "M", "AAA"], za
    p1 = sorted(syms, key=lambda s: (symbol_perm_key(s, 1), s))
    p2 = sorted(syms, key=lambda s: (symbol_perm_key(s, 2), s))
    assert p1 == sorted(syms, key=lambda s: (symbol_perm_key(s, 1), s))
    assert p1 != p2 or p1 != za  # at least not both identical to Z-A by accident
    print("ok order helpers")


if __name__ == "__main__":
    test_breakout_pct_formula()
    test_rank_entry_actions_buys_desc_closes_first()
    test_same_ts_max1_rank_picks_larger_breakout()
    test_same_ts_without_rank_keeps_stream_order()
    test_breakout_atr_formula()
    test_same_ts_atr_rank_prefers_quiet_break_over_noisy_rip()
    test_same_ts_za_picks_zzz()
    test_same_ts_least_picks_smaller_breakout()
    test_symbol_perm_is_deterministic_and_can_flip()
    test_order_helpers_sort_stream()
    print("ALL ENTRY RANK PASS")
