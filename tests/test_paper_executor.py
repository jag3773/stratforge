#!/usr/bin/env python3
"""Unit tests: PaperExecutor cash math + no deadlock under nested lock calls."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies.paper_executor import PaperExecutor


def test_buy_debits_notional_and_fee():
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001)
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        # notional=250, fee=0.25, cash=749.75, position value=250, equity=999.75
        assert abs(ex.cash - 749.75) < 1e-6, ex.cash
        assert "BTC" in ex.positions
        assert abs(ex.equity() - 999.75) < 1e-6, ex.equity()
        snap = ex.snapshot()
        assert snap["trades"] == 1
        assert abs(snap["equity"] - 999.75) < 1e-6


def test_close_credits_notional_minus_fee():
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        qty = ex.positions["ETH"]["qty"]
        ex.apply([{"type": "CLOSE", "symbol": "ETH", "price": 60.0, "reason": "x"}])
        assert "ETH" not in ex.positions
        # entry notional 250 fee 0.25; exit notional 300 fee 0.3
        # cash after buy 749.75; after close +299.7 = 1049.45
        assert abs(ex.cash - 1049.45) < 1e-6, ex.cash
        assert ex.wins == 1


def test_snapshot_no_deadlock():
    """Regression: Lock() + equity() inside snapshot deadlocked the live node."""
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j)
        ex.update_price("BTC", 1.0)
        done = []

        def worker():
            try:
                for _ in range(100):
                    ex.snapshot()
                    ex.equity()
                    ex.apply([{"type": "BUY", "symbol": "BTC", "price": 1.0, "reason": "t"}])
                    if "BTC" in ex.positions:
                        ex.apply([{"type": "CLOSE", "symbol": "BTC", "price": 1.1, "reason": "x"}])
                done.append(True)
            except Exception:
                traceback.print_exc()
                done.append(False)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive(), "deadlock detected"
        assert all(done)


def test_feed_bucket_dedup():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from strategies.live_data_feed import (
        LiveDataFeed,
        _floor_4h_ms,
        bar_ts_ns_from_bucket_ms,
    )

    emitted = []
    with tempfile.TemporaryDirectory() as td:
        health = os.path.join(td, "health.json")
        feed = LiveDataFeed(
            ["BTC"],
            on_4h_bar=lambda s, b: emitted.append((s, b["ts"])),
            health_path=health,
        )
        b0 = _floor_4h_ms(1_700_000_000_000)
        feed._last_emitted["BTC"] = b0
        bar = {
            "ts": bar_ts_ns_from_bucket_ms(b0),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        assert feed._emit("BTC", bar, "t") is False
        bar2 = {
            "ts": bar_ts_ns_from_bucket_ms(b0 + 4 * 3600 * 1000),
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 1,
        }
        assert feed._emit("BTC", bar2, "t") is True
        assert len(emitted) == 1


def test_restore_from_journal_preserves_book():
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001,
                           write_opening=True)
        ex.apply([{"type": "BUY", "symbol": "PUMP", "price": 0.002, "reason": "t"}])
        ex.apply([{"type": "BUY", "symbol": "ADA", "price": 0.18, "reason": "t"}])
        cash1 = ex.cash
        pos1 = {k: dict(v) for k, v in ex.positions.items()}
        # New process-like executor on same journal
        ex2 = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001)
        info = ex2.restore_from_journal()
        assert info["restored"] is True
        assert abs(ex2.cash - cash1) < 1e-6, (ex2.cash, cash1)
        assert set(ex2.positions) == set(pos1) == {"PUMP", "ADA"}
        assert abs(ex2.positions["ADA"]["qty"] - pos1["ADA"]["qty"]) < 1e-9
        assert ex2.trades == 2


def test_cash_deposit_restores():
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001,
                           write_opening=True)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 2000.0, "reason": "t"}])
        cash_before = ex.cash
        dep = ex.deposit_cash(250.0, reason="unit_test")
        assert abs(dep["cash"] - (cash_before + 250.0)) < 1e-6
        assert abs(ex.start_usd - 1250.0) < 1e-6
        ex2 = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001)
        info = ex2.restore_from_journal()
        assert info["restored"] is True
        assert abs(ex2.cash - (cash_before + 250.0)) < 1e-6, (ex2.cash, cash_before)
        assert abs(ex2.start_usd - 1250.0) < 1e-6
        assert "ETH" in ex2.positions


def test_rotate_when_full_underwater_only():
    from strategies.signal_engine import SignalEngine, SignalConfig

    cfg = SignalConfig(
        mode="breakout",
        channel_period=5,
        max_concurrent=1,
        rotate_when_full=True,
        rotate_min_hold_bars=1,
        bar_minutes=240,
        trend_filter=False,
        cooldown_bars=0,
        slow_ema_period=3,
        fast_ema_period=2,
        atr_stop_mult=0.0,
        exit_mode="slow_ema",
        long_only=True,
        use_adx_filter=False,
    )
    eng = SignalEngine(cfg, ["AAA", "BBB"])
    # Warm both with rising then channel so breakout can fire.
    def bar(ts, c, h=None, l=None):
        h = c if h is None else h
        l = c if l is None else l
        return {"ts": ts, "open": c, "high": h, "low": l, "close": c, "volume": 1.0}

    base = 1_700_000_000_000_000_000
    step = 4 * 3600 * 1_000_000_000
    # Enough bars to init indicators (ADX needs 2*period etc.) — feed many
    for i in range(80):
        px = 10.0 + i * 0.01
        eng.on_bar("AAA", bar(base + i * step, px, px + 0.02, px - 0.02), emit=False)
        eng.on_bar("BBB", bar(base + i * step, px, px + 0.02, px - 0.02), emit=False)

    positions = {
        "AAA": {"side": "LONG", "entry": 20.0, "ts": base},  # underwater vs mark ~10.8
    }
    eng.sync_slots_from_positions(positions)
    eng.last_px["AAA"] = 9.0  # clearly underwater
    # Force breakout on BBB: fill channel then spike
    st = eng.states["BBB"]
    st.hh = [10.0] * cfg.channel_period
    st.ll = [9.0] * cfg.channel_period
    st.bars_since_trade = 999
    # Make sure ADX/RSI/EMA ready already from warm
    ts = base + 80 * step
    acts = eng.on_bar(
        "BBB",
        bar(ts, 50.0, 51.0, 49.0),
        emit=True,
        positions=positions,
    )
    types = [(a["type"], a["symbol"], a.get("reason")) for a in acts]
    assert any(t[0] == "CLOSE" and t[1] == "AAA" and t[2] == "rotate_out" for t in types), types
    assert any(t[0] == "BUY" and t[1] == "BBB" for t in types), types

    # Winner must not be rotated
    eng2 = SignalEngine(cfg, ["AAA", "BBB"])
    for i in range(80):
        px = 10.0 + i * 0.01
        eng2.on_bar("AAA", bar(base + i * step, px, px + 0.02, px - 0.02), emit=False)
        eng2.on_bar("BBB", bar(base + i * step, px, px + 0.02, px - 0.02), emit=False)
    positions2 = {"AAA": {"side": "LONG", "entry": 5.0, "ts": base}}
    eng2.sync_slots_from_positions(positions2)
    eng2.last_px["AAA"] = 12.0  # winner
    st2 = eng2.states["BBB"]
    st2.hh = [10.0] * cfg.channel_period
    st2.ll = [9.0] * cfg.channel_period
    st2.bars_since_trade = 999
    acts2 = eng2.on_bar("BBB", bar(ts, 50.0, 51.0, 49.0), emit=True, positions=positions2)
    assert acts2 == [] or not any(a.get("reason") == "rotate_out" for a in acts2), acts2


def test_residual_cash_partial_buy_not_skipped_by_float():
    """Regression: cash-capped buys used to no-op when cost was 1 ulp above cash.

    After ~3x max_alloc fills, leftover cash < target notional; every subsequent
    BUY was silently dropped. Repro numbers match post-DOT paper book.
    """
    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001)
        ex.cash = 244.39816909285224
        ex.positions = {
            "PUMP": {"side": "LONG", "entry": 0.002177, "qty": 114836.93155718879, "ts": 0, "fee": 0.25},
            "ADA": {"side": "LONG", "entry": 0.1799, "qty": 1398.090649299118, "ts": 0, "fee": 0.25},
            "DOT": {"side": "LONG", "entry": 0.8389, "qty": 301.979349283959, "ts": 0, "fee": 0.25},
        }
        ex.last_price = {"PUMP": 0.0022, "ADA": 0.18, "DOT": 0.84}
        ex.trades = 3
        cash_before = ex.cash
        ex.apply([{"type": "BUY", "symbol": "AVAX", "price": 6.869, "reason": "breakout_long"}])
        assert "AVAX" in ex.positions, "residual-cash BUY must fill (was float-skip bug)"
        assert ex.trades == 4
        assert ex.cash >= -1e-9
        leftover_inc = 6.869 * 1e-8 * 1.001
        assert ex.cash < leftover_inc + 1e-9 or ex.cash < 0.01, ex.cash
        assert cash_before - ex.cash > 200.0
        # Second residual path: small leftover after a full alloc buy path
        ex2 = PaperExecutor(start_usd=1000.0, max_alloc_pct=0.25, journal_path=j, fee_rate=0.001)
        ex2.cash = 170.15
        ex2.positions = {"AAA": {"side": "LONG", "entry": 1.0, "qty": 1000.0, "ts": 0, "fee": 0.0}}
        ex2.last_price = {"AAA": 1.0}
        ex2.apply([{"type": "BUY", "symbol": "POL", "price": 0.0782, "reason": "t"}])
        assert "POL" in ex2.positions
        assert ex2.cash >= -1e-9
        leftover2 = 0.0782 * 1e-8 * 1.001
        assert ex2.cash < leftover2 + 1e-9 or ex2.cash < 0.01, ex2.cash


def test_dust_buy_below_min_notional_is_not_journaled():
    from risk_limits import RiskLimits

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(
            start_usd=3.0,
            max_alloc_pct=1.0,
            journal_path=j,
            fee_rate=0.001,
            limits=RiskLimits(
                min_notional_usd=5.0,
                max_alloc_pct=1.0,
                daily_loss_halt_pct=0.0,
                daily_loss_halt_usd=0.0,
                halt_file=os.path.join(td, "no-halt"),
            ),
        )
        ex.update_price("NEAR", 4.0)
        ex.apply([{"type": "BUY", "symbol": "NEAR", "price": 4.0, "reason": "t"}])
        assert "NEAR" not in ex.positions
        text = open(j).read() if os.path.exists(j) else ""
        assert "RISK_DENY" not in text, text


def test_real_risk_deny_still_journaled():
    from risk_limits import RiskLimits

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(
            start_usd=1000.0,
            max_alloc_pct=0.25,
            journal_path=j,
            fee_rate=0.001,
            limits=RiskLimits(
                min_notional_usd=5.0,
                allowed_symbols=("BTC",),
                daily_loss_halt_pct=0.0,
                daily_loss_halt_usd=0.0,
                halt_file=os.path.join(td, "no-halt"),
            ),
        )
        ex.update_price("ETH", 100.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 100.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        text = open(j).read()
        assert "RISK_DENY" in text, text


def test_paper_buy_enforces_daily_loss_halt_pct():
    from risk_limits import RiskLimits
    from strategies.day_risk import utc_today

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = PaperExecutor(
            start_usd=1000.0,
            max_alloc_pct=0.25,
            journal_path=j,
            fee_rate=0.001,
            limits=RiskLimits(
                min_notional_usd=5.0,
                daily_loss_halt_usd=0.0,
                daily_loss_halt_pct=0.10,
                halt_file=os.path.join(td, "no-halt"),
            ),
        )
        ex.day.day_utc = utc_today()
        ex.day.day_start_equity = 1000.0
        ex.cash = 850.0

        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        assert "BTC" not in ex.positions
        text = open(j).read()
        assert "RISK_DENY" in text, text
        assert "hit halt $100.00" in text, text


if __name__ == "__main__":
    test_buy_debits_notional_and_fee()
    print("ok buy")
    test_close_credits_notional_minus_fee()
    print("ok close")
    test_snapshot_no_deadlock()
    print("ok deadlock")
    test_feed_bucket_dedup()
    print("ok dedup")
    test_restore_from_journal_preserves_book()
    print("ok restore")
    test_cash_deposit_restores()
    print("ok deposit")
    test_rotate_when_full_underwater_only()
    print("ok rotate")
    test_residual_cash_partial_buy_not_skipped_by_float()
    print("ok residual cash")
    test_dust_buy_below_min_notional_is_not_journaled()
    print("ok dust deny silent")
    test_real_risk_deny_still_journaled()
    print("ok real deny journaled")
    test_paper_buy_enforces_daily_loss_halt_pct()
    print("ok paper daily loss halt pct")
    print("ALL PASS")
