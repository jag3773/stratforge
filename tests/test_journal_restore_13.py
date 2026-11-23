"""
Tests for fix #13: restore_from_journal preserves entry ts and entry_fee.

Two bugs:
  1. restore_from_journal used int(time.time()*1e9) for the position ts instead
     of the journal entry's ts, resetting rotate_min_hold_bars on restart.
  2. BOOK_SYNC journal write omitted 'fee', so entry_fee became 0.0 on restart,
     overstating realized PnL.

Never talks to Coinbase.  Never creates LIVE_APPROVED.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_executor(td, start_usd=1000.0):
    from risk_limits import RiskLimits
    from strategies.live_executor import LiveExecutor

    j = os.path.join(td, "j.jsonl")
    return LiveExecutor(
        start_usd=start_usd,
        journal_path=j,
        dry_run=True,
        limits=RiskLimits(
            halt_file=os.path.join(td, "nohalt"),
            max_total_notional_usd=10_000,
        ),
    )


# ---------------------------------------------------------------------------
# Bug 1 — restore preserves entry ts (not reset to time.time())
# ---------------------------------------------------------------------------

def test_restore_preserves_entry_ts():
    """
    After a BUY is journaled and then restore_from_journal() is called, the
    position ts must equal the ts from the journal entry, not a fresh
    int(time.time()*1e9).  A stale ts would reset rotate_min_hold_bars.
    """
    with tempfile.TemporaryDirectory() as td:
        from risk_limits import RiskLimits
        from strategies.live_executor import LiveExecutor

        j = os.path.join(td, "j.jsonl")

        # Build a journal with a known past ts (5 minutes ago).
        past_ts_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_ts_ns = int(past_ts_dt.timestamp() * 1_000_000_000)
        past_ts_iso = past_ts_dt.isoformat()

        journal_entry = {
            "action": "SHADOW_BUY",
            "symbol": "BTC",
            "price": 50000.0,
            "qty": 0.01,
            "fee": 0.5,
            "cash": 500.0,
            "ts": past_ts_iso,
            "mode": "SHADOW",
            "dry_run": True,
        }
        Path(j).write_text(json.dumps(journal_entry) + "\n")

        ex = LiveExecutor(
            start_usd=1000.0,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(
                halt_file=os.path.join(td, "nohalt"),
                max_total_notional_usd=10_000,
            ),
        )
        info = ex.restore_from_journal()
        assert info["restored"]
        assert "BTC" in ex.positions, "BTC position must be restored"

        restored_ts_ns = ex.positions["BTC"]["ts"]

        # The restored ts must match the journal ts (within 1 second tolerance
        # for any sub-second rounding), not be a fresh time.time().
        assert abs(restored_ts_ns - past_ts_ns) < 1_000_000_000, (
            f"restored ts {restored_ts_ns} differs from journal ts "
            f"{past_ts_ns} by more than 1 s — hold-timer was reset on restart"
        )

        # Sanity: it must NOT be close to the current wall clock.
        now_ns = int(time.time() * 1_000_000_000)
        assert abs(restored_ts_ns - now_ns) > 60 * 1_000_000_000, (
            "restored ts equals time.time() — position hold-timer was reset"
        )


def test_restore_preserves_ts_through_book_sync():
    """
    BOOK_SYNC processed during restore must also carry the journal entry ts,
    not reset to time.time().
    """
    with tempfile.TemporaryDirectory() as td:
        from risk_limits import RiskLimits
        from strategies.live_executor import LiveExecutor

        j = os.path.join(td, "j.jsonl")

        past_ts_dt = datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
        past_ts_ns = int(past_ts_dt.timestamp() * 1_000_000_000)
        past_ts_iso = past_ts_dt.isoformat()

        lines = [
            json.dumps({
                "action": "BOOK_SYNC",
                "cash": 800.0,
                "positions": {
                    "ETH": {"qty": 2.0, "entry": 2000.0, "fee": 1.5},
                },
                "ts": past_ts_iso,
                "mode": "SHADOW",
                "dry_run": True,
            }),
        ]
        Path(j).write_text("\n".join(lines) + "\n")

        ex = LiveExecutor(
            start_usd=1000.0,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(
                halt_file=os.path.join(td, "nohalt"),
                max_total_notional_usd=10_000,
            ),
        )
        info = ex.restore_from_journal()
        assert info["restored"]
        assert "ETH" in ex.positions

        restored_ts_ns = ex.positions["ETH"]["ts"]
        assert abs(restored_ts_ns - past_ts_ns) < 1_000_000_000, (
            f"BOOK_SYNC restore reset ts: got {restored_ts_ns}, expected ~{past_ts_ns}"
        )


# ---------------------------------------------------------------------------
# Bug 2 — BOOK_SYNC journal write includes fee; restore reads it back
# ---------------------------------------------------------------------------

def test_book_sync_journal_write_includes_fee():
    """
    When _book_sync_from_exchange writes a BOOK_SYNC entry, the positions dict
    in the journal must include 'fee' so that entry_fee survives restart.
    """
    with tempfile.TemporaryDirectory() as td:
        from risk_limits import RiskLimits
        from strategies.live_executor import LiveExecutor

        j = os.path.join(td, "j.jsonl")
        ex = LiveExecutor(
            start_usd=1000.0,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(
                halt_file=os.path.join(td, "nohalt"),
                max_total_notional_usd=10_000,
            ),
        )
        # Inject a position with a known fee directly.
        ex.positions = {
            "SOL": {"side": "LONG", "entry": 100.0, "qty": 5.0, "ts": int(time.time() * 1e9), "fee": 2.5},
        }
        ex._book_sync_from_exchange(
            exchange_positions={"SOL": 5.0},
            exchange_cash=750.0,
            reason="test",
        )

        # Read the written journal and find the BOOK_SYNC entry.
        entries = [json.loads(l) for l in Path(j).read_text().splitlines() if l.strip()]
        sync_entries = [e for e in entries if e.get("action") == "BOOK_SYNC"]
        assert sync_entries, "BOOK_SYNC entry must be written"
        sync = sync_entries[-1]
        sol_spec = sync.get("positions", {}).get("SOL", {})
        assert "fee" in sol_spec, (
            f"BOOK_SYNC journal entry missing 'fee' for SOL; got keys: {list(sol_spec)}"
        )
        assert abs(float(sol_spec["fee"]) - 2.5) < 1e-9, (
            f"BOOK_SYNC fee mismatch: expected 2.5, got {sol_spec['fee']}"
        )


def test_restore_preserves_entry_fee_through_book_sync():
    """
    After a BOOK_SYNC the entry_fee must survive restore_from_journal.
    Before the fix, the BOOK_SYNC journal write omitted 'fee', so
    restore set entry_fee = 0.0 and overstated realized PnL.
    """
    with tempfile.TemporaryDirectory() as td:
        from risk_limits import RiskLimits
        from strategies.live_executor import LiveExecutor

        j = os.path.join(td, "j.jsonl")

        # Craft a journal that has a BOOK_SYNC with fee in the spec.
        lines = [
            json.dumps({
                "action": "BOOK_SYNC",
                "cash": 900.0,
                "positions": {
                    "LINK": {"qty": 10.0, "entry": 15.0, "fee": 3.0},
                },
                "ts": "2026-06-01T00:00:00+00:00",
                "mode": "SHADOW",
                "dry_run": True,
            }),
        ]
        Path(j).write_text("\n".join(lines) + "\n")

        ex = LiveExecutor(
            start_usd=1000.0,
            journal_path=j,
            dry_run=True,
            limits=RiskLimits(
                halt_file=os.path.join(td, "nohalt"),
                max_total_notional_usd=10_000,
            ),
        )
        info = ex.restore_from_journal()
        assert info["restored"]
        assert "LINK" in ex.positions

        fee = ex.positions["LINK"].get("fee", 0.0)
        assert abs(fee - 3.0) < 1e-9, (
            f"entry_fee lost on BOOK_SYNC restore: expected 3.0, got {fee}. "
            "Realized PnL will be overstated."
        )


if __name__ == "__main__":
    test_restore_preserves_entry_ts()
    test_restore_preserves_ts_through_book_sync()
    test_book_sync_journal_write_includes_fee()
    test_restore_preserves_entry_fee_through_book_sync()
    print("ALL PASS")
