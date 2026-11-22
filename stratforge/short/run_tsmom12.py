#!/usr/bin/env python3
"""
Score tsmom12_weekly on the same windows as bear_regime_cover.

  # run from the checkout
  .venv/bin/python short/run_tsmom12.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from short.ledger import ShortLedger  # noqa: E402
from short.replay import (  # noqa: E402
    RAW_DIR,
    _max_dd,
    _regime_slices,
    _to_dt,
    btc_buyhold,
    btc_short_hold,
    load_4h_bars,
    load_cfg,
)
from short.tsmom import Tsmom12Engine, TsmomConfig  # noqa: E402

WINDOWS = [
    ("2021-11-01", "2022-12-31", "bear2022"),
    ("2023-07-18", "2026-07-18", "long_window"),
    ("2024-03-01", "2024-09-01", "dd_2024"),
    ("2025-10-01", "2026-07-15", "dd_2025_26"),
    ("2020-08-01", "2026-08-13", "full"),
]


def _iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).date().isoformat()


def run_tsmom_replay(
    symbols: list[str],
    *,
    cfg: dict,
    start: str,
    end: str,
    start_usd: float,
    fee_rate: float,
    funding_per_8h: float,
    integer_contracts: bool,
) -> dict:
    tcfg = TsmomConfig.from_dict(cfg)
    trade_start = _to_dt(start)
    trade_end = _to_dt(end)
    lookback = int(tcfg.lookback_weeks)
    data_start = trade_start - timedelta(weeks=lookback + 4) if trade_start else None
    trade_start_ns = int(trade_start.timestamp() * 1e9) if trade_start else None

    per: dict[str, list[dict]] = {}
    for s in symbols:
        bars = load_4h_bars(s, start=data_start, end=trade_end, raw_dir=RAW_DIR)
        if bars:
            per[s] = bars

    engine = Tsmom12Engine(tcfg, list(per.keys()))
    ledger = ShortLedger(
        start_usd=start_usd,
        max_leverage=float(cfg.get("max_leverage", 1.0)),
        fee_rate=fee_rate,
        funding_per_8h=funding_per_8h,
        maintenance_margin_pct=float(cfg.get("maintenance_margin_pct", 0.50)),
        integer_contracts=integer_contracts,
    )

    stream: list[tuple[int, int, str, dict]] = []
    for s, bars in per.items():
        prio = 0 if s == tcfg.regime_symbol else 1
        for b in bars:
            stream.append((int(b["ts"]), prio, s, b))
    stream.sort(key=lambda x: (x[0], x[1], x[2]))

    curve: list[tuple[int, float]] = []
    regime_curve: list[tuple[int, float, bool]] = []
    open_max = 0
    first_trade = last_trade = None
    n_bars = 0

    for ts, _prio, sym, bar in stream:
        emit = True if trade_start_ns is None else ts >= trade_start_ns
        px = float(bar["close"])
        ledger.update_price(sym, px, ts)
        if emit:
            ledger.apply_funding(ts)
            ledger.maybe_liquidate(ts, px)
        actions = engine.on_bar(sym, bar, emit=emit, positions=ledger.positions() if emit else None)
        if emit and actions:
            for a in actions:
                kind = str(a.get("type") or "").upper()
                apx = float(a.get("price", px))
                if kind == "COVER":
                    rec = ledger.cover(a["symbol"], apx, ts, str(a.get("reason") or "cover"))
                    if rec:
                        first_trade = first_trade or ts
                        last_trade = ts
                elif kind == "SHORT":
                    eq = ledger.equity()
                    alloc = float(a.get("alloc_pct") or engine.alloc_pct(a["symbol"]))
                    rec = ledger.short(
                        a["symbol"],
                        apx,
                        ts,
                        target_notional=eq * alloc,
                        reason=str(a.get("reason") or "tsmom12"),
                    )
                    if rec:
                        first_trade = first_trade or ts
                        last_trade = ts
            ledger.maybe_liquidate(ts, px)
        n_bars += 1
        if emit:
            eq = ledger.equity()
            if curve and curve[-1][0] == ts:
                curve[-1] = (ts, eq)
                regime_curve[-1] = (ts, eq, bool(engine.regime_bear))
            else:
                curve.append((ts, eq))
                regime_curve.append((ts, eq, bool(engine.regime_bear)))
            open_max = max(open_max, len(ledger.lots))

    end_eq = ledger.equity()
    return {
        "strategy_id": str(cfg.get("strategy_id", "tsmom12_weekly")),
        "engine": "tsmom12_weekly_margin1x",
        "account_type": "MARGIN_1X",
        "symbols": list(per.keys()),
        "start_usd": start_usd,
        "end_equity": round(end_eq, 4),
        "end_cash": round(ledger.cash, 4),
        "net_return_pct": round((end_eq / start_usd - 1.0) * 100.0, 4) if start_usd else 0.0,
        "max_dd_pct": round(_max_dd(curve) * 100.0, 4),
        "trades": ledger.trades,
        "wins": ledger.wins,
        "losses": ledger.losses,
        "liquidations": ledger.liquidations,
        "fees_paid": round(ledger.fees_paid, 4),
        "funding_paid": round(ledger.funding_paid, 4),
        "open_positions": len(ledger.lots),
        "open_max": open_max,
        "bars": n_bars,
        "first_trade_ts": first_trade,
        "last_trade_ts": last_trade,
        "trade_start": start,
        "trade_end": end,
        "warmup_from": data_start.date().isoformat() if data_start else None,
        "fee_rate": fee_rate,
        "funding_per_8h": funding_per_8h,
        "max_leverage": float(cfg.get("max_leverage", 1.0)),
        "integer_contracts": integer_contracts,
        "config": {k: v for k, v in cfg.items() if not str(k).startswith("_")},
        "regime": _regime_slices(regime_curve),
        "open_lots": ledger.positions(),
        "lookback_weeks": lookback,
        "cat_stop_pct": tcfg.cat_stop_pct,
        "atr_stop_mult": 0.0,
    }


def run_one(cfg: dict, start: str, end: str, label: str, symbols: list[str]) -> dict:
    start_usd = float(cfg.get("start_usd", 10_000.0))
    fee = float(cfg.get("fee_rate", 0.0004))
    fund = float(cfg.get("funding_per_8h", 0.00003))
    out = run_tsmom_replay(
        symbols,
        cfg=cfg,
        start=start,
        end=end,
        start_usd=start_usd,
        fee_rate=fee,
        funding_per_8h=fund,
        integer_contracts=bool(cfg.get("integer_contracts", False)),
    )
    bh_end, bh_pct = btc_buyhold(start_usd, start, end)
    sh = btc_short_hold(start_usd, start, end, fee_rate=fee, funding_per_8h=fund)
    out["label"] = label
    out["first_trade"] = _iso(out.get("first_trade_ts"))
    out["last_trade"] = _iso(out.get("last_trade_ts"))
    out["benchmarks"] = {
        "cash_return_pct": 0.0,
        "btc_buyhold_end": round(bh_end, 2),
        "btc_buyhold_return_pct": round(bh_pct, 4),
        "btc_shorthold_end": round(sh["end_equity"], 2),
        "btc_shorthold_return_pct": round(sh["return_pct"], 4),
        "btc_shorthold_funding": round(sh["funding_paid"], 2),
    }
    out["beats_cash"] = out["end_equity"] > start_usd
    out["beats_btc_shorthold"] = out["end_equity"] > sh["end_equity"]
    return out


def _print(out: dict) -> None:
    rg = out.get("regime") or {}
    print(
        f"{out['strategy_id']:20} {out.get('label'):14} "
        f"ret={out['net_return_pct']:+7.2f}% dd={out['max_dd_pct']:5.1f}% "
        f"trades={out['trades']:3d} W/L={out['wins']}/{out['losses']} "
        f"fees=${out['fees_paid']:.0f} fund=${out['funding_paid']:.0f} "
        f"bull={rg.get('bull_return_pct', 0):+.1f}% "
        f"first={out.get('first_trade')}"
    )


def main() -> int:
    cfg = load_cfg("config/short/tsmom12_weekly.json")
    symbols = ["BTC", "ETH"]
    rows = []
    for start, end, label in WINDOWS:
        out = run_one(cfg, start, end, label, symbols)
        _print(out)
        rows.append(out)

    # kill card vs pre-committed thresholds
    by = {r["label"]: r for r in rows}
    kills = []
    if by["bear2022"]["net_return_pct"] <= 0:
        kills.append("2022 <= 0")
    if by["dd_2024"]["net_return_pct"] < -3.0:
        kills.append("2024 < -3%")
    if by["dd_2025_26"]["net_return_pct"] < 15.0:
        kills.append("crash < +15%")
    years = 6.1
    flips = by["full"]["trades"]
    if flips / years > 4.0:
        kills.append(f"round-trips/year {flips/years:.1f} > 4")
    print()
    print("KILL:" if kills else "PASS kill card:", ", ".join(kills) if kills else "none")

    payload = {
        "strategy_id": "tsmom12_weekly",
        "config": "config/short/tsmom12_weekly.json",
        "start_usd": 10000.0,
        "fee_rate": 0.0004,
        "funding_per_8h": 0.00003,
        "lookback_weeks": 52,
        "atr_stop_mult": 0.0,
        "cat_stop_pct": 0.0,
        "kill_card": kills,
        "windows": rows,
    }
    out_path = ROOT / "results" / "backtests" / "short" / "tsmom12_weekly.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
