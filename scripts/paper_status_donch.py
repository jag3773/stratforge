#!/usr/bin/env python3
"""Donch paper soak status. Isolated journal/health. No LIVE block. No SSOT paper files."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(
    os.environ.get("PRO_TRADER_ROOT")
    or os.environ.get("NAUTILUS_ROOT")
    or Path(__file__).resolve().parents[1]
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paper_status import compute_status, display_ts  # noqa: E402

JOURNAL = ROOT / "data" / "execution_log_donch.jsonl"
NODE_HEALTH = ROOT / "data" / "node_health_donch.json"
FEED_HEALTH = ROOT / "data" / "feed_health_donch.json"
DNA = ROOT / "config" / "donch_d20_10.json"


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _load_journal():
    if not JOURNAL.exists():
        return []
    entries = []
    for line in JOURNAL.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def main() -> int:
    dna = _load_json(DNA) or {}
    entries = _load_journal()
    status = compute_status(entries)
    opening = status.get("opening") or {}
    nh = _load_json(NODE_HEALTH)
    fh = _load_json(FEED_HEALTH)

    if nh and nh.get("equity") is not None:
        portfolio = float(nh["equity"])
        cash = float(nh.get("cash", status["cash"] or 0.0))
    else:
        portfolio = float(status["equity"] or 0.0)
        cash = float(status["cash"] or 0.0)

    strategy = (nh or {}).get("strategy") or dna.get("strategy_id") or "donch_d20_10"
    start_cap = float(opening.get("capital", 500))
    start_date = opening.get("start_date", "n/a")
    contributed = float(status.get("contributed_capital") or start_cap)
    pnl = portfolio - contributed
    pnl_pct = (pnl / contributed * 100.0) if contributed else 0.0

    print("=== Donch Paper Status ===")
    print(f"Strategy: {strategy} (paper soak — NOT SSOT, not LIVE)")
    print(
        "Knobs: daily Donchian entry_n="
        f"{dna.get('entry_n', 20)} exit_n={dna.get('exit_n', 10)} "
        f"max_alloc_pct={dna.get('max_alloc_pct', 0.25)} "
        f"max_concurrent={dna.get('max_concurrent', 8)} "
        f"rotate_when_full={dna.get('rotate_when_full', True)} "
        f"rotate_min_hold_bars={dna.get('rotate_min_hold_bars', 3)} "
        "atr_stop_mult=0.0 fee=10bps CASH"
    )
    print(f"Start capital: ${start_cap:.2f} on {start_date}")
    print(
        f"Portfolio value: ${portfolio:.2f}  ({pnl:+.2f} / {pnl_pct:+.2f}% vs contributed)"
    )
    print(f"Cash: ${cash:.2f}")

    if nh:
        print(
            f"Node: bars={nh.get('bars_seen')} trades={nh.get('trades')} "
            f"last={nh.get('last_bar_symbol')} @ {display_ts(nh.get('ts'))}"
        )
    else:
        print("Node: unavailable (showing journal fallback)")

    if fh:
        print(
            f"Feed: cycle={fh.get('cycle')} emitted={fh.get('bars_emitted')} "
            f"ok={fh.get('fetch_ok')} fail={fh.get('fetch_fail')} ts={display_ts(fh.get('ts'))}"
        )

    print("\nOpen Positions:")
    pos_list = (nh or {}).get("positions")
    if isinstance(pos_list, list) and pos_list and not status["positions"]:
        for sym in pos_list:
            print(f"  {sym}")
    elif status["positions"]:
        for sym, p in status["positions"].items():
            print(f"  {sym}: {p['qty']:.6g} @ ${p.get('entry_price', 0):.6g}")
    else:
        print("  (none)")

    print(f"\nTrades in last 24h: {len(status['recent_trades'])}")
    for t in status["recent_trades"][-10:]:
        act = t.get("action", "")
        sym = t.get("symbol", "")
        price = t.get("price")
        pnl_t = t.get("pnl")
        reason = t.get("reason", "")
        if "BUY" in act:
            print(f"  BUY {sym} @ ${price} ({reason})")
        elif "CLOSE" in act:
            print(f"  CLOSE {sym} @ ${price} PnL=${pnl_t} ({reason})")

    print(f"\nTotal lifetime trades: {status['total_trades']}")
    print("Clock: next decision at UTC daily close (8pm ET / 7pm ET winter)")
    print("=====================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
