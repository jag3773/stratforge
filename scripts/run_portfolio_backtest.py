#!/usr/bin/env python3
"""
Single-account portfolio backtest (SignalEngine CASH replay on .venv).

Replaces archived strategies/portfolio_single.py (Nautilus 1.221 BacktestEngine).

Usage:
  .venv/bin/python scripts/run_portfolio_backtest.py \\
    --config config/trend_rotation_25_max8_rotate.json \\
    --start 2023-07-18 --end 2026-07-18
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stratforge  # noqa: E402
from strategies.research_replay import (  # noqa: E402
    btc_buyhold_end,
    load_strategy_cfg,
    run_portfolio_replay,
)
from strategies.universe import load_live_universe  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="SignalEngine CASH portfolio backtest")
    ap.add_argument("--config", default="config/trend_rotation_25_max8_rotate.json")
    ap.add_argument("--start", default="2023-07-18")
    ap.add_argument("--end", default="2026-07-18")
    ap.add_argument("--start-usd", type=float, default=1000.0)
    ap.add_argument("--fee", type=float, default=0.004, help="Taker-style fee fraction")
    ap.add_argument("--pairs", default=None, help="Comma symbols (default live universe)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = load_strategy_cfg(args.config)
    pairs = (
        [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
        if args.pairs
        else load_live_universe()
    )
    r = run_portfolio_replay(
        pairs,
        strategy_cfg=cfg,
        start=args.start,
        end=args.end,
        start_usd=args.start_usd,
        fee_rate=args.fee,
    )
    btc_end, btc_ret, _, _ = btc_buyhold_end(args.start_usd, args.start, args.end)
    scfg = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    out = {
        "engine": "signal_engine_cash_replay",
        "account_type": "CASH",
        "strategy": Path(cfg.get("_config_path") or args.config).stem,
        "config_path": cfg.get("_config_path"),
        "config": scfg,
        "window": {"start": args.start, "end": args.end, "warmup_from": r.data_start_warmup},
        "start_usd": args.start_usd,
        "end_equity": round(r.end_equity, 2),
        "end_cash": round(r.end_cash, 2),
        "positions_mv": round(r.positions_mv, 2),
        "strategy_return_pct": round(r.net_return_pct * 100.0, 2),
        "max_dd_pct": round(r.max_dd_pct * 100.0, 2),
        "trades": r.trades,
        "wins": r.wins,
        "losses": r.losses,
        "open_positions": r.open_positions,
        "open_max": r.open_max,
        "btc_buyhold_end": round(btc_end, 2),
        "btc_buyhold_return_pct": round(btc_ret, 2),
        "beats_btc_buyhold": r.end_equity > btc_end,
        "coins_with_data": len(r.symbols),
        "bars": r.bars,
        "fee_rate": args.fee,
        "open_lots": r.positions,
    }
    print("=== Portfolio backtest (SignalEngine CASH) ===")
    print(f"Strategy: {out['strategy']}")
    print(
        f"Params: max_alloc={scfg.get('max_alloc_pct')} max_concurrent={scfg.get('max_concurrent')} "
        f"rotate_when_full={scfg.get('rotate_when_full')} rotate_min_hold_bars={scfg.get('rotate_min_hold_bars')} "
        f"exit_mode={scfg.get('exit_mode')} atr_stop_mult={scfg.get('atr_stop_mult')}"
    )
    print(f"Window: {args.start} → {args.end} (warmup {r.data_start_warmup})")
    print(f"End equity: ${out['end_equity']:.2f} ({out['strategy_return_pct']:+.2f}%)")
    print(f"BTC buy-hold: ${out['btc_buyhold_end']:.2f} ({out['btc_buyhold_return_pct']:+.2f}%) beats={out['beats_btc_buyhold']}")
    print(f"Trades: {out['trades']} wins={out['wins']} losses={out['losses']} open={out['open_positions']} open_max={out['open_max']}")
    print(f"Max DD: {out['max_dd_pct']}% | coins={out['coins_with_data']} bars={out['bars']}")
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2))
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
