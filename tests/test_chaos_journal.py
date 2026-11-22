#!/usr/bin/env python3
"""Chaos / journal hardness. Never talks to Coinbase. Never creates LIVE_APPROVED."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"


def test_restore_skips_truncated_last_line():
    from risk_limits import RiskLimits
    from strategies.live_executor import LiveExecutor

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        ex = LiveExecutor(
            start_usd=250,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(halt_file=os.path.join(td, "nohalt"), max_total_notional_usd=2000),
        )
        ex.update_price("BTC", 100)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100, "reason": "t"}])
        cash, qty = ex.cash, ex.positions["BTC"]["qty"]
        with open(j, "a") as f:
            f.write('{"action": "SHADOW_BUY", "symbol": "ETH", "pr')  # truncated
        ex2 = LiveExecutor(
            start_usd=250,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(halt_file=os.path.join(td, "nohalt"), max_total_notional_usd=2000),
        )
        info = ex2.restore_from_journal()
        assert info["restored"]
        assert "BTC" in ex2.positions
        assert "ETH" not in ex2.positions
        assert abs(ex2.cash - cash) < 1e-6
        assert abs(ex2.positions["BTC"]["qty"] - qty) < 1e-8
    print("ok truncated last line")


def test_opening_balance_wipes_prior_in_same_file():
    from risk_limits import RiskLimits
    from strategies.live_executor import LiveExecutor

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        Path(j).write_text(
            json.dumps({"action": "SHADOW_BUY", "symbol": "BTC", "price": 1, "qty": 1, "cash": 10})
            + "\n"
            + json.dumps({"action": "OPENING_BALANCE", "capital": 250.0})
            + "\n"
        )
        ex = LiveExecutor(
            start_usd=999,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(halt_file=os.path.join(td, "nohalt")),
        )
        info = ex.restore_from_journal()
        assert abs(ex.cash - 250) < 1e-9
        assert not ex.positions
        assert info["trades"] == 0
    print("ok opening balance reset")


def test_reconcile_empty_fetch_does_not_adopt():
    from risk_limits import RiskLimits
    from strategies.live_executor import LiveExecutor

    with tempfile.TemporaryDirectory() as td:
        ex = LiveExecutor(
            start_usd=250,
            journal_path=os.path.join(td, "j.jsonl"),
            dry_run=False,  # will be forced dry by bridge
            limits=RiskLimits(halt_file=os.path.join(td, "nohalt"), max_total_notional_usd=2000),
        )
        # force live-like reconcile path
        ex.dry_run = False
        ex.positions = {"BTC": {"side": "LONG", "entry": 1, "qty": 1, "fee": 0}}
        rec = ex.apply_reconcile({"ok": False, "error": "timeout", "positions": {}, "cash_usd": 0})
        assert rec.ok is False
        assert ex.reconciled is False
        assert "BTC" in ex.positions
    print("ok fail-closed reconcile")


def test_book_sync_replaces_qty_and_cash():
    from risk_limits import RiskLimits
    from strategies.live_executor import LiveExecutor

    with tempfile.TemporaryDirectory() as td:
        j = os.path.join(td, "j.jsonl")
        Path(j).write_text(
            json.dumps({"action": "OPENING_BALANCE", "capital": 250.0})
            + "\n"
            + json.dumps(
                {
                    "action": "LIVE_BUY",
                    "symbol": "PUMP",
                    "price": 0.005126,
                    "qty": 12192.74287943,
                    "cash": 187.44,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "action": "BOOK_SYNC",
                    "cash": 62.583622,
                    "positions": {
                        "PUMP": {"qty": 12128.0, "entry": 0.005092},
                        "AAVE": {"qty": 0.482, "entry": 128.01},
                    },
                    "reason": "adopt coinbase wallet",
                }
            )
            + "\n"
        )
        ex = LiveExecutor(
            start_usd=250,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(halt_file=os.path.join(td, "nohalt"), max_total_notional_usd=2000),
        )
        info = ex.restore_from_journal()
        assert info["restored"]
        assert abs(ex.cash - 62.583622) < 1e-9
        assert set(ex.positions) == {"PUMP", "AAVE"}
        assert abs(ex.positions["PUMP"]["qty"] - 12128.0) < 1e-9
        assert abs(ex.positions["AAVE"]["qty"] - 0.482) < 1e-9
    print("ok book sync restore")


if __name__ == "__main__":
    test_restore_skips_truncated_last_line()
    test_opening_balance_wipes_prior_in_same_file()
    test_reconcile_empty_fetch_does_not_adopt()
    test_book_sync_replaces_qty_and_cash()
    print("ALL CHAOS PASS")
