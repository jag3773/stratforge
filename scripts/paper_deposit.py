#!/usr/bin/env python3
"""Deposit cash into the paper journal (CASH_DEPOSIT) without placing trades.

Restores the book from the journal, applies deposit, journals it.
Restart stratforge-paper.service afterward so the node reloads cash.

Usage:
  .venv/bin/python scripts/paper_deposit.py 250
  .venv/bin/python scripts/paper_deposit.py 250 --reason "capital add"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stratforge  # noqa: E402
from strategies.paper_executor import PaperExecutor  # noqa: E402

PAPER_JOURNAL = ROOT / "data" / "execution_log.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser(description="CASH_DEPOSIT into paper journal")
    ap.add_argument("amount", type=float, help="USD to add (positive)")
    ap.add_argument("--reason", default="manual_deposit")
    ap.add_argument("--journal", default=None, help="Override journal path")
    args = ap.parse_args()
    if args.amount <= 0:
        print("amount must be > 0", file=sys.stderr)
        return 2

    jpath = args.journal or str(PAPER_JOURNAL)
    ex = PaperExecutor(
        start_usd=250.0,
        max_alloc_pct=0.25,
        journal_path=jpath,
        fee_rate=0.001,
        write_opening=False,
    )
    info = ex.restore_from_journal()
    before_cash = ex.cash
    before_eq = ex.equity()
    before_start = ex.start_usd
    dep = ex.deposit_cash(args.amount, reason=args.reason)
    print("=== Paper cash deposit ===")
    print(f"journal: {jpath}")
    print(
        f"restored: {info.get('restored')} lines={info.get('lines')} "
        f"positions={info.get('positions')}"
    )
    print(
        f"before: cash=${before_cash:.2f} equity≈${before_eq:.2f} "
        f"start_usd=${before_start:.2f}"
    )
    print(
        f"deposit: +${dep['amount']:.2f} ({dep['reason']}) → "
        f"cash=${dep['cash']:.2f} equity≈${dep['equity']:.2f} "
        f"start_usd=${dep['start_usd']:.2f}"
    )
    print("NOTE: restart stratforge-paper.service so the node picks this up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
