#!/usr/bin/env python3
"""
Staggered-start backtest with paper-status-style final-day report.

Canonical engine (Phase 0 / .venv):
  strategies.research_replay — SignalEngine + CASH PaperExecutor
  (same decisions as live paper; no Nautilus 1.221 BacktestEngine)

Usage:
  .venv/bin/python scripts/staggered_backtest_status.py \\
      --start 2026-07-02 --end 2026-08-02 --label m01 \\
      --config config/trend_rotation_25_max8_rotate.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.research_replay import (  # noqa: E402
    btc_buyhold_end,
    load_strategy_cfg,
    run_portfolio_replay,
)
from strategies.universe import load_live_universe  # noqa: E402

START_BALANCE = 1000.0
CONFIG_PATH = ROOT / "config" / "trend_rotation_25_max8_rotate.json"
OUT_DIR = ROOT / "data" / "staggered_starts"


def _iso_ts(ts: int | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc).isoformat()
    except Exception:
        return None


def run_backtest(
    start: datetime,
    end: datetime,
    cfg: dict,
    pairs: list[str],
    taker_fee: float = 0.004,
    maker_fee: float = 0.002,  # unused (paper is taker-style); kept for CLI compat
):
    del maker_fee  # PaperExecutor uses single fee_rate (taker-style)
    start_s = start.date().isoformat()
    end_s = end.date().isoformat()

    result = run_portfolio_replay(
        pairs,
        strategy_cfg=cfg,
        start=start_s,
        end=end_s,
        start_usd=START_BALANCE,
        fee_rate=float(taker_fee),
    )

    btc_end, btc_ret, _, _ = btc_buyhold_end(START_BALANCE, start_s, end_s)
    equity = float(result.end_equity)
    cash = float(result.end_cash)
    positions_mv = float(result.positions_mv)
    positions_cost = float(result.positions_cost)
    strat_ret = (equity / START_BALANCE - 1.0) * 100.0
    n_closed = len(result.closed_trades)
    wins = int(result.wins)
    losses = int(result.losses)
    win_rate = (wins / n_closed * 100.0) if n_closed else 0.0

    end_day = end.date()
    final_day_trades = []
    for e in result.trade_events:
        ts = e.get("ts")
        if ts is None:
            continue
        dt = datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)
        if dt.date() != end_day:
            continue
        final_day_trades.append(
            {
                "action": e.get("action"),
                "symbol": e.get("symbol"),
                "price": e.get("price"),
                "qty": e.get("qty"),
                "pnl": e.get("pnl"),
                "ts": _iso_ts(ts),
            }
        )

    by_coin: dict[str, list] = {}
    for c in result.closed_trades:
        sym = c["symbol"]
        by_coin.setdefault(sym, [0, 0.0])
        by_coin[sym][0] += 1
        by_coin[sym][1] += float(c.get("realized_pnl") or 0.0)

    scfg = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    open_positions = result.positions
    exposure_ratio = (positions_mv / equity) if equity > 0 else None
    over_allocated = bool(positions_cost > START_BALANCE * 1.01 and cash < 0)

    summary = {
        "strategy": scfg.get("strategy_id")
        or Path(cfg.get("_config_path") or CONFIG_PATH).stem,
        "engine": "signal_engine_cash_replay",
        "account_type": "CASH",
        "equity_method": "cash_plus_mtm",
        "taker_fee": taker_fee,
        "maker_fee": taker_fee,  # single fee path
        "config_path": cfg.get("_config_path") or str(CONFIG_PATH),
        "config": scfg,
        "window": {
            "start": start_s,
            "end": end_s,
            "data_start_warmup": result.data_start_warmup,
            "trade_start_ns": int(start.timestamp() * 1e9),
        },
        "start_balance": START_BALANCE,
        "equity": round(equity, 2),
        "equity_from_pnl": None,
        "usd_cash": round(cash, 2),
        "positions_cost": round(positions_cost, 2),
        "positions_mv": round(positions_mv, 2),
        "gross_exposure": round(positions_mv, 2),
        "exposure_ratio": round(exposure_ratio, 4) if exposure_ratio is not None else None,
        "over_allocated": over_allocated,
        "balance_total_usd": round(cash, 2),
        "unrealized_pnl": round(positions_mv - positions_cost, 2),
        "cash": round(cash, 2),
        "locked": 0.0,
        "strategy_return_pct": round(strat_ret, 2),
        "btc_buyhold_end": round(btc_end, 2),
        "btc_buyhold_return_pct": round(btc_ret, 2),
        "beats_btc_buyhold": equity > btc_end,
        "closed_trades": n_closed,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "max_dd_pct": round(result.max_dd_pct * 100.0, 2),
        "open_max": result.open_max,
        "coins_with_data": len(result.symbols),
        "coins_traded": len(by_coin) + len({p["symbol"] for p in open_positions}),
        "open_positions": [
            {
                "symbol": p["symbol"],
                "qty": p["qty"],
                "entry_price": p["entry_price"],
                "last_price": p.get("last_price"),
                "market_value": p.get("market_value"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "ts_opened": _iso_ts(p.get("ts_opened")) if isinstance(p.get("ts_opened"), int) else p.get("ts_opened"),
            }
            for p in open_positions
        ],
        "final_day_trades": final_day_trades,
        "final_day_fills": [],
        "first_trade_ts": _iso_ts(result.first_trade_ts),
        "last_trade_ts": _iso_ts(result.last_trade_ts),
        "n_bars": result.bars,
        "first_bar_ts": _iso_ts(result.first_ts),
        "last_bar_ts": _iso_ts(result.last_ts),
        "trades_by_coin": {
            k: {"trades": v[0], "realized_pnl": round(v[1], 2)}
            for k, v in sorted(by_coin.items(), key=lambda x: -x[1][1])
        },
    }
    return summary


def print_status(summary: dict, label: str) -> None:
    w = summary["window"]
    cfg = summary.get("config") or {}
    print("=== Nautilus Backtest Status (staggered) ===")
    print(f"Strategy: {summary.get('strategy')}")
    print(f"Engine: {summary.get('engine')} | Account: CASH spot")
    print(
        f"Params: max_alloc_pct={cfg.get('max_alloc_pct')} atr_stop_mult={cfg.get('atr_stop_mult')} "
        f"exit_mode={cfg.get('exit_mode')} channel_period={cfg.get('channel_period')} "
        f"bar_minutes={cfg.get('bar_minutes')} max_concurrent={cfg.get('max_concurrent')} "
        f"rotate_when_full={cfg.get('rotate_when_full')} "
        f"rotate_min_hold_bars={cfg.get('rotate_min_hold_bars')}"
    )
    print(f"Window: {w['start']} → {w['end']}  ({label})")
    if w.get("data_start_warmup"):
        print(f"Indicator warmup from: {w['data_start_warmup']} (entries gated until {w['start']})")
    print(f"Start capital: ${summary['start_balance']:.2f} on {w['start']}")
    print(f"End equity: ${summary['equity']:.2f}  ({summary['strategy_return_pct']:+.2f}%)")
    print(
        f"Equity = cash ${summary.get('cash', 0):.2f} + positions MTM ${summary.get('positions_mv', 0):.2f}"
    )
    print(
        f"Positions cost ${summary.get('positions_cost', 0):.2f} | "
        f"unrealized ${summary.get('unrealized_pnl', 0):.2f} | "
        f"exposure_ratio {summary.get('exposure_ratio')} | open_max={summary.get('open_max')}"
    )
    print(
        f"BTC buy-hold end: ${summary['btc_buyhold_end']:.2f} "
        f"({summary['btc_buyhold_return_pct']:+.2f}%) | beats_btc: {summary['beats_btc_buyhold']}"
    )
    print(
        f"Closed trades: {summary['closed_trades']} | win rate: {summary['win_rate']}% | "
        f"coins traded: {summary['coins_traded']} | max_dd={summary.get('max_dd_pct')}%"
    )
    if summary.get("first_trade_ts"):
        print(f"First trade: {summary['first_trade_ts']}")
    print(f"Bars loaded: {summary.get('n_bars')} | coins with data: {summary.get('coins_with_data')}")

    print("\nOpen Positions (as of end):")
    ops = summary.get("open_positions") or []
    if ops:
        for p in ops:
            last = p.get("last_price")
            mv = p.get("market_value")
            up = p.get("unrealized_pnl")
            extra = ""
            if last is not None:
                extra = f" last=${last:.6g} mv=${mv:.2f} uPnL=${up:.2f}"
            print(f"  {p['symbol']}: {p['qty']:.6g} @ ${p['entry_price']:.6g}{extra}")
    else:
        print("  (none)")

    print(f"\nTrades on final day ({w['end']}):")
    fd = summary.get("final_day_trades") or []
    if fd:
        for t in fd:
            act = t.get("action", "")
            sym = t.get("symbol", "")
            price = t.get("price")
            pnl = t.get("pnl")
            if act == "BUY":
                print(f"  BUY {sym} @ ${price}")
            elif act == "CLOSE":
                extra = f" PnL=${pnl}" if pnl is not None else ""
                print(f"  CLOSE {sym} @ ${price}{extra}")
            else:
                print(f"  {act} {sym} @ ${price}")
    else:
        print("  (none)")

    print(f"\nTotal lifetime trades (window): {summary['closed_trades'] + len(ops)}")
    print(f"Closed round-trips: {summary['closed_trades']}")
    print("===========================================")


def main():
    default_pairs = ",".join(load_live_universe())
    ap = argparse.ArgumentParser(
        description="Staggered-start trend_rotation SignalEngine CASH status"
    )
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--label", default="mXX", help="e.g. m01")
    ap.add_argument("--out", default=None, help="JSON output path")
    ap.add_argument(
        "--config",
        default=str(CONFIG_PATH.relative_to(ROOT)),
        help="Strategy JSON (default production max8_rotate)",
    )
    ap.add_argument("--pairs", default=default_pairs)
    ap.add_argument(
        "--taker-fee",
        type=float,
        default=0.004,
        help="Fee fraction applied on paper fills (default 0.004 = 40 bps)",
    )
    ap.add_argument(
        "--maker-fee",
        type=float,
        default=None,
        help="Ignored (compat); paper path uses --taker-fee only",
    )
    ap.add_argument("--rotate-min-hold-bars", type=int, default=None)
    ap.add_argument("--max-concurrent", type=int, default=None)
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    cfg = load_strategy_cfg(cfg_path)
    if args.rotate_min_hold_bars is not None:
        cfg["rotate_min_hold_bars"] = int(args.rotate_min_hold_bars)
    if args.max_concurrent is not None:
        cfg["max_concurrent"] = int(args.max_concurrent)
    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    taker = float(args.taker_fee)
    maker = float(args.maker_fee) if args.maker_fee is not None else taker / 2.0

    os.chdir(ROOT)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    summary = run_backtest(start, end, cfg, pairs, taker_fee=taker, maker_fee=maker)
    summary["label"] = args.label

    out = Path(args.out) if args.out else OUT_DIR / f"{args.label}_{args.start}_to_{args.end}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    summary["_out"] = str(out)

    print_status(summary, args.label)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
