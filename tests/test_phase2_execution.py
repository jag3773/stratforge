#!/usr/bin/env python3
"""Phase 2 tests: risk gates, dry-run bridge, LiveExecutor shadow path, LIVE refuse."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Force safe env for tests
os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"
os.environ.pop("TRADING_HALTED", None)


def test_risk_long_only_and_caps():
    from risk_limits import RiskLimits, check_order

    lim = RiskLimits(
        max_position_notional_usd=100,
        max_total_notional_usd=200,
        max_alloc_pct=0.25,
        max_concurrent=2,
        long_only=True,
        allowed_symbols=("BTC", "ETH"),
        daily_loss_halt_usd=50,
    )
    ok = check_order("BTC-USD", "buy", 80, equity=1000, open_positions=0, limits=lim)
    assert ok.approved, ok

    big = check_order("BTC-USD", "buy", 150, equity=1000, open_positions=0, limits=lim)
    assert not big.approved

    short = check_order("BTC-USD", "sell", 50, is_close=False, limits=lim)
    assert not short.approved

    close = check_order("BTC-USD", "sell", 50, is_close=True, limits=lim)
    assert close.approved

    ban = check_order("DOGE-USD", "buy", 10, equity=1000, limits=lim)
    assert not ban.approved

    print("ok risk")


def test_halt_file():
    from risk_limits import RiskLimits, check_order, is_trading_halted

    with tempfile.TemporaryDirectory() as td:
        halt = os.path.join(td, "HALT")
        lim = RiskLimits(halt_file=halt)
        assert is_trading_halted(lim)[0] is False
        Path(halt).write_text("stop\n")
        assert is_trading_halted(lim)[0] is True
        r = check_order("BTC-USD", "buy", 10, equity=1000, limits=lim)
        assert not r.approved
        close = check_order("BTC-USD", "sell", 10, is_close=True, limits=lim)
        assert close.approved, close
    print("ok halt")


def test_bridge_dry_run():
    import ccxt_bridge

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "b.jsonl")
        st = ccxt_bridge.status()
        assert st["dry_run"] is True
        r = ccxt_bridge.place_order(
            "BTC-USD", "buy", 0.001, price=60000.0, journal_path=j
        )
        assert r["action"] == "DRY_RUN_ORDER"
        assert r["status"] == "SIMULATED"
        lines = Path(j).read_text().strip().splitlines()
        assert len(lines) == 1
    print("ok bridge dry-run")


PUMP_VENUE = {
    "id": "b2e999b2-6f2a-450f-a466-12d926a0b818",
    "status": "closed",
    "amount": 12128.0,
    "filled": 12128.0,
    "remaining": 0.0,
    "average": 0.005092,
    "price": 0.005092,
    "cost": 61.755776,
    "fee": {"cost": "0.741069312", "currency": "USD"},
}


def test_resolved_fill_never_uses_requested_size():
    import ccxt_bridge

    assert ccxt_bridge.resolved_fill(None) == 0.0
    assert ccxt_bridge.resolved_fill({"filled": None, "amount": 12192.74}) == 0.0
    assert ccxt_bridge.resolved_fill({"status": None, "amount": 0.488}) == 0.0
    assert ccxt_bridge.resolved_fill({"filled": 0, "amount": 12128}) == 0.0
    assert abs(ccxt_bridge.resolved_fill({"filled": 12128.0, "amount": 99999}) - 12128.0) < 1e-9
    print("ok resolved fill")


def test_resolved_cost_fee_avg_from_venue_payload():
    import ccxt_bridge

    echo = {"status": None, "filled": None, "amount": 12192.74287943, "price": 0.005126}
    assert ccxt_bridge.resolved_avg(echo) is None
    assert ccxt_bridge.resolved_cost(echo) is None
    assert ccxt_bridge.resolved_fee(echo) == (None, None)

    assert abs(ccxt_bridge.resolved_avg(PUMP_VENUE) - 0.005092) < 1e-12
    assert abs(ccxt_bridge.resolved_cost(PUMP_VENUE) - 61.755776) < 1e-9
    fee, ccy = ccxt_bridge.resolved_fee(PUMP_VENUE)
    assert abs(fee - 0.741069312) < 1e-9
    assert ccy == "USD"

    norm = ccxt_bridge.normalize_ccxt_order(echo)
    assert norm["complete"] is False
    assert norm["cost"] is None
    assert norm["fee"] is None

    ok = ccxt_bridge.normalize_ccxt_order(PUMP_VENUE)
    assert ok["complete"] is True
    assert abs(ok["filled"] - 12128.0) < 1e-9
    assert abs(ok["cost"] - 61.755776) < 1e-9
    assert abs(ok["fee"] - 0.741069312) < 1e-9
    print("ok venue cost extractors")


def test_live_executor_shadow_buy_close():
    from strategies.live_executor import LiveExecutor
    from risk_limits import RiskLimits

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "live.jsonl")
        lim = RiskLimits(
            max_position_notional_usd=500,
            max_total_notional_usd=2000,
            max_alloc_pct=0.25,
            max_concurrent=4,
            long_only=True,
            halt_file=os.path.join(td, "nohalt"),
        )
        ex = LiveExecutor(
            start_usd=1000,
            max_alloc_pct=0.25,
            journal_path=j,
            dry_run=True,
            limits=lim,
            mode_label="SHADOW",
        )
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        assert "BTC" in ex.positions
        # notional 250 + fee 0.25
        assert abs(ex.cash - 749.75) < 1e-6, ex.cash
        ex.apply([{"type": "CLOSE", "symbol": "BTC", "price": 110.0, "reason": "x"}])
        assert "BTC" not in ex.positions
        text = Path(j).read_text()
        assert "SHADOW_BUY" in text
        assert "SHADOW_CLOSE" in text
        assert "DRY_RUN_ORDER" in text
        snap = ex.snapshot()
        assert snap["dry_run"] is True
        assert snap["trades"] == 1
    print("ok shadow executor")


def test_live_executor_risk_deny():
    from strategies.live_executor import LiveExecutor
    from risk_limits import RiskLimits

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "live.jsonl")
        lim = RiskLimits(
            max_position_notional_usd=10,  # tiny
            max_total_notional_usd=10,
            max_alloc_pct=0.25,
            halt_file=os.path.join(td, "nohalt"),
        )
        ex = LiveExecutor(
            start_usd=1000,
            max_alloc_pct=0.25,
            journal_path=j,
            dry_run=True,
            limits=lim,
            mode_label="SHADOW",
        )
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        # target notional min(250, 10)=10 — should pass if 10 <= 10
        # max_position 10, alloc would be 250 -> capped to 10 -> approved
        # Use even tighter
        lim2 = RiskLimits(
            max_position_notional_usd=1,
            max_total_notional_usd=1,
            max_alloc_pct=0.25,
            min_notional_usd=5,
            halt_file=os.path.join(td, "nohalt"),
        )
        ex2 = LiveExecutor(
            start_usd=1000,
            max_alloc_pct=0.25,
            journal_path=j + "2",
            dry_run=True,
            limits=lim2,
            mode_label="SHADOW",
        )
        ex2.update_price("ETH", 50.0)
        ex2.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" not in ex2.positions
        assert "RISK_DENY" in Path(j + "2").read_text()
    print("ok risk deny")


def test_live_mode_refused_without_flags():
    """Import node helper: LIVE without flags exits."""
    import importlib.util

    # Simulate env
    os.environ["LIVE_TRADING"] = "false"
    os.environ["SIMULATION_MODE"] = "true"
    from strategies import live_node as ln

    from strategies.config_load import load_config

    cfg = load_config(ROOT / "config" / "live_production.json")
    try:
        ln._build_executor("live", cfg)
        raise AssertionError("should have refused LIVE")
    except SystemExit as e:
        assert "refused" in str(e).lower() or "LIVE" in str(e)
    print("ok live refused")


def test_paper_mode_still_builds():
    from strategies import live_node as ln

    from strategies.config_load import load_config

    cfg = load_config(ROOT / "config" / "live_paper.json")
    ex, journal = ln._build_executor("paper", cfg)
    assert ex.__class__.__name__ == "PaperExecutor"
    assert "execution_log.jsonl" in journal
    print("ok paper build")


if __name__ == "__main__":
    test_risk_long_only_and_caps()
    test_halt_file()
    test_bridge_dry_run()
    test_resolved_fill_never_uses_requested_size()
    test_resolved_cost_fee_avg_from_venue_payload()
    test_live_executor_shadow_buy_close()
    test_live_executor_risk_deny()
    test_live_mode_refused_without_flags()
    test_paper_mode_still_builds()
    print("ALL PHASE2 PASS")
