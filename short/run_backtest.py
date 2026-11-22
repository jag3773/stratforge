#!/usr/bin/env python3
"""
Run bear_regime_cover research replay.

  # run from the checkout
  .venv/bin/python short/run_backtest.py
  .venv/bin/python short/run_backtest.py --start 2021-11-01 --end 2022-12-31 --label bear2022
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from short.replay import btc_buyhold, btc_short_hold, load_cfg, run_short_replay  # noqa: E402


def _iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).date().isoformat()


def run_one(args, start: str, end: str, label: str, symbols: list[str]) -> dict:
    cfg = load_cfg(args.config)
    r = run_short_replay(
        symbols,
        cfg=cfg,
        start=start,
        end=end,
        start_usd=args.start_usd,
        fee_rate=args.fee,
        funding_per_8h=args.funding,
        integer_contracts=args.integer_contracts,
    )
    bh_end, bh_pct = btc_buyhold(args.start_usd, start, end)
    sh = btc_short_hold(
        args.start_usd,
        start,
        end,
        fee_rate=args.fee,
        funding_per_8h=args.funding,
    )
    out = r.to_dict()
    out["label"] = label
    out["first_trade"] = _iso(r.first_trade_ts)
    out["last_trade"] = _iso(r.last_trade_ts)
    out["benchmarks"] = {
        "cash_return_pct": 0.0,
        "btc_buyhold_end": round(bh_end, 2),
        "btc_buyhold_return_pct": round(bh_pct, 4),
        "btc_shorthold_end": round(sh["end_equity"], 2),
        "btc_shorthold_return_pct": round(sh["return_pct"], 4),
        "btc_shorthold_funding": round(sh["funding_paid"], 2),
    }
    out["beats_cash"] = r.end_equity > args.start_usd
    out["beats_btc_shorthold"] = r.end_equity > sh["end_equity"]
    # downturn goal: beat cash AND beat always-short only matters in mixed/bull windows
    return out


def _print(out: dict) -> None:
    print(f"=== {out['strategy_id']} / {out.get('label')} ===")
    print(
        f"Params: short_only universe={out['symbols']} lev={out['max_leverage']} "
        f"atr_stop={out['config'].get('atr_stop_mult')} ema={out['config'].get('slow_ema_period')} "
        f"btc_alloc={out['config'].get('btc_alloc_pct')} eth_alloc={out['config'].get('eth_alloc_pct')} "
        f"fee={out['fee_rate']} funding_8h={out['funding_per_8h']} integer={out['integer_contracts']}"
    )
    print(f"Window: {out['trade_start']} → {out['trade_end']} warmup {out['warmup_from']}")
    print(
        f"End equity: ${out['end_equity']:.2f} ({out['net_return_pct']:+.2f}%)  "
        f"cash=${out['end_cash']:.2f} open={out['open_positions']} open_max={out['open_max']}"
    )
    b = out["benchmarks"]
    print(
        f"Bench: cash 0% | BTC buyhold {b['btc_buyhold_return_pct']:+.2f}% | "
        f"BTC always-short {b['btc_shorthold_return_pct']:+.2f}%"
    )
    print(
        f"Trades: {out['trades']} wins={out['wins']} losses={out['losses']} "
        f"liq={out['liquidations']} first={out.get('first_trade')} last={out.get('last_trade')}"
    )
    print(
        f"Max DD {out['max_dd_pct']:.2f}% | fees ${out['fees_paid']:.2f} | "
        f"funding ${out['funding_paid']:.2f}"
    )
    rg = out.get("regime") or {}
    if rg:
        print(
            f"Regime: bear_frac={rg.get('bear_frac', 0):.2%} "
            f"bear_ret={rg.get('bear_return_pct', 0):+.2f}% "
            f"bull_ret={rg.get('bull_return_pct', 0):+.2f}%"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="bear_regime_cover short research")
    ap.add_argument("--config", default="config/short/bear_regime_cover.json")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--start-usd", type=float, default=10_000.0)
    ap.add_argument("--fee", type=float, default=0.0004)
    ap.add_argument("--funding", type=float, default=0.00003)
    ap.add_argument("--btc-only", action="store_true")
    ap.add_argument("--integer-contracts", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    symbols = ["BTC"] if args.btc_only else ["BTC", "ETH"]
    windows = []
    if args.start and args.end:
        windows.append((args.start, args.end, args.label or f"{args.start}_{args.end}"))
    else:
        windows = [
            ("2021-11-01", "2022-12-31", "bear2022"),
            ("2023-07-18", "2026-07-18", "long_window"),
            ("2020-07-01", "2026-08-13", "full"),
        ]

    reports = []
    for start, end, label in windows:
        out = run_one(args, start, end, label, symbols)
        _print(out)
        print()
        reports.append(out)

    payload = {
        "strategy_id": "bear_regime_cover",
        "config": args.config,
        "start_usd": args.start_usd,
        "fee_rate": args.fee,
        "funding_per_8h": args.funding,
        "symbols": symbols,
        "integer_contracts": bool(args.integer_contracts),
        "windows": reports,
    }
    out_path = Path(args.out) if args.out else ROOT / "results" / "backtests" / "short" / "bear_regime_cover_v0.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
