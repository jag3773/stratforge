#!/usr/bin/env python3
"""Underwater slow-EMA exit is off unless slow_ema_exit_underwater is set."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.signal_engine import SignalConfig, SignalEngine  # noqa: E402


def _bar(ts: int, close: float) -> dict:
    return {
        "ts": ts,
        "open": close,
        "high": close,
        "low": close * 0.99,
        "close": close,
        "volume": 1.0,
    }


def _engine(underwater: bool) -> SignalEngine:
    cfg = SignalConfig(
        mode="breakout",
        channel_period=3,
        fast_ema_period=3,
        slow_ema_period=3,
        adx_period=2,
        atr_period=2,
        rsi_period=2,
        atr_stop_mult=0.0,
        cooldown_bars=0,
        use_adx_filter=False,
        trend_filter=True,
        long_only=True,
        exit_mode="slow_ema",
        slow_ema_exit_underwater=underwater,
        max_concurrent=8,
        rotate_when_full=False,
    )
    return SignalEngine(cfg, ["WLD"])


def _warm(eng: SignalEngine, px: float, n: int = 12) -> None:
    for i in range(n):
        eng.on_bar("WLD", _bar(i * 1_000_000_000, px), emit=False)


def _held(eng: SignalEngine, close: float, entry: float) -> list[dict]:
    return eng.on_bar(
        "WLD",
        _bar(90 * 1_000_000_000, close),
        emit=True,
        positions={"WLD": {"side": "LONG", "entry": entry}},
    )


def test_default_does_not_sell_underwater_below_ema():
    eng = _engine(False)
    _warm(eng, 100.0)
    acts = _held(eng, 80.0, 100.0)
    assert acts == [], acts


def test_flag_sells_underwater_below_ema_when_book_not_full():
    eng = _engine(True)
    _warm(eng, 100.0)
    acts = _held(eng, 80.0, 100.0)
    assert len(acts) == 1, acts
    assert acts[0]["type"] == "CLOSE"
    assert acts[0]["reason"] == "slow_ema_exit"
    assert acts[0]["symbol"] == "WLD"


def test_flag_does_not_sell_underwater_still_above_ema():
    eng = _engine(True)
    _warm(eng, 50.0)
    # Red vs a 100 entry, but still above the EMA warmed at 50.
    acts = _held(eng, 80.0, 100.0)
    assert acts == [], acts


def test_from_dict_defaults_flag_off():
    cfg = SignalConfig.from_dict({"exit_mode": "slow_ema", "atr_stop_mult": 0.0})
    assert cfg.slow_ema_exit_underwater is False
    on = SignalConfig.from_dict({"slow_ema_exit_underwater": True})
    assert on.slow_ema_exit_underwater is True
