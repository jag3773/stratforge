"""exit_mode other than slow_ema must not raise AttributeError."""
from __future__ import annotations

from strategies.signal_engine import SignalConfig, SignalEngine


def _bar(ts: int, close: float) -> dict:
    return {
        "ts": ts,
        "open": close,
        "high": close,
        "low": close * 0.99,
        "close": close,
        "volume": 1.0,
    }


def test_atr_exit_mode_does_not_raise():
    cfg = SignalConfig(
        mode="breakout",
        channel_period=3,
        fast_ema_period=3,
        slow_ema_period=3,
        adx_period=2,
        atr_period=2,
        rsi_period=2,
        atr_stop_mult=1.0,
        atr_tp_mult=2.0,
        cooldown_bars=0,
        use_adx_filter=False,
        trend_filter=False,
        long_only=True,
        exit_mode="atr",
        max_concurrent=8,
        rotate_when_full=False,
    )
    eng = SignalEngine(cfg, ["WLD"])
    for i in range(12):
        eng.on_bar("WLD", _bar(i * 1_000_000_000, 100.0), emit=False)
    eng.on_bar(
        "WLD",
        _bar(13 * 1_000_000_000, 130.0),
        emit=True,
        positions={"WLD": {"side": "LONG", "entry": 100.0, "qty": 1.0}},
    )
