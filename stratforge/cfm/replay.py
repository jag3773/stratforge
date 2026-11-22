"""Drive FuturesLedger + strategy intents. Next-open fills. Intra-bar liq/stops.

Does not import strategies.* or short.*.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cfm.bars import load_4h_bars, load_cde_4h
from cfm.ledger import FuturesLedger
from cfm.strats import Intent
from cfm.venue import cde_multipliers, load_venue, multiplier

ROOT = Path(__file__).resolve().parents[1]


def _align(bar_map: dict[str, list[dict]]) -> list[tuple[int, dict[str, dict]]]:
    """Union of timestamps → {sym: bar}."""
    ts_set: set[int] = set()
    by_ts: dict[str, dict[int, dict]] = {}
    for sym, bars in bar_map.items():
        by_ts[sym] = {}
        for b in bars:
            by_ts[sym][b["ts"]] = b
            ts_set.add(b["ts"])
    out = []
    for ts in sorted(ts_set):
        row = {sym: by_ts[sym][ts] for sym in bar_map if ts in by_ts[sym]}
        if row:
            out.append((ts, row))
    return out


def _max_dd(curve: list[tuple[int, float]]) -> float:
    if not curve:
        return 0.0
    peak = curve[0][1]
    worst = 0.0
    for _, eq in curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            worst = max(worst, (peak - eq) / peak)
    return float(worst)


def btc_buyhold_multiple(bars: list[dict], trade_start_ns: int) -> dict:
    w = [b for b in bars if b["ts"] >= trade_start_ns]
    if not w:
        return {"btc_start": 0.0, "btc_end": 0.0, "btc_multiple": 1.0}
    a, b = float(w[0]["close"]), float(w[-1]["close"])
    return {"btc_start": a, "btc_end": b, "btc_multiple": (b / a if a > 0 else 1.0)}


def run_replay(
    strategy,
    symbols: list[str],
    start: str,
    end: str,
    start_usd: float = 1000.0,
    max_leverage: float = 4.0,
    fee_rate: float = 0.0004,
    funding_per_8h: float = 0.00003,
    integer_contracts: bool = True,
    warmup_days: int = 400,
    signal_lag_bars: int = 0,
    tape: str = "spot",
    funding_interval_hours: float = 8.0,
) -> dict[str, Any]:
    venue = load_venue()
    if tape == "cde":
        all_m = cde_multipliers()
        mults = {s.upper(): all_m.get(s.upper(), multiplier(s, venue)) for s in symbols}
    else:
        mults = {s.upper(): multiplier(s, venue) for s in symbols}
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    from datetime import timedelta

    warm = (start_dt - timedelta(days=int(warmup_days))).strftime("%Y-%m-%d")
    warm_ns = int((start_dt - timedelta(days=int(warmup_days))).timestamp() * 1_000_000_000)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=23, minute=59)
    end_ns = int(end_dt.timestamp() * 1_000_000_000)
    bar_map = {}
    for s in symbols:
        if tape == "cde":
            full = load_cde_4h(s.upper())
        else:
            full = load_4h_bars(s.upper(), None, None)
        bar_map[s.upper()] = [b for b in full if warm_ns <= b["ts"] <= end_ns]
    aligned = _align(bar_map)
    trade_start_ns = int(start_dt.timestamp() * 1_000_000_000)
    ledger = FuturesLedger(
        start_usd=float(start_usd),
        max_leverage=float(max_leverage),
        fee_rate=float(fee_rate),
        funding_per_8h=float(funding_per_8h),
        funding_interval_hours=float(funding_interval_hours),
        integer_contracts=bool(integer_contracts),
        multipliers=mults,
    )
    pending: list[Intent] = []
    last_intents: list[Intent] = []
    curve: list[tuple[int, float]] = []
    n_bars = 0
    last_row: dict[str, dict] = {}
    live_started = False
    lag_left = 0

    for ts, row in aligned:
        n_bars += 1
        last_row = row
        for sym, b in row.items():
            ledger.update_price(sym, float(b["close"]), ts)
        if ts >= trade_start_ns:
            ledger.apply_funding(ts)
            for sym, b in row.items():
                ledger.maybe_liquidate_bar(sym, float(b["high"]), float(b["low"]), ts)
                ledger.maybe_stop_bar(sym, float(b["high"]), float(b["low"]), ts)
            if not live_started:
                pending = list(last_intents)
                live_started = True
                lag_left = 0
            if lag_left > 0:
                lag_left -= 1
            elif pending:
                for it in pending:
                    px = float(row[it.symbol]["open"]) if it.symbol in row else 0.0
                    if px <= 0:
                        continue
                    eq = max(ledger.equity(), 0.0)
                    notion = eq * float(it.leverage)
                    if it.hold_contracts:
                        lot = ledger.lots.get(it.symbol)
                        if lot and lot.side == it.side:
                            continue
                        if it.side == "FLAT" or it.leverage <= 0:
                            ledger.rebalance(
                                it.symbol, "FLAT", px, ts, 0.0, reason=it.reason, stop_px=it.stop_px
                            )
                            continue
                    ledger.rebalance(
                        it.symbol, it.side, px, ts, notion, reason=it.reason, stop_px=it.stop_px
                    )
                pending = []
            eq = ledger.equity()
            curve.append((ts, eq))
            if eq <= 1e-6:
                break
        closes = {sym: float(b["close"]) for sym, b in row.items()}
        highs = {sym: float(b["high"]) for sym, b in row.items()}
        lows = {sym: float(b["low"]) for sym, b in row.items()}
        intents = strategy.on_close(ts, closes, highs, lows)
        if intents:
            last_intents = intents
            if ts >= trade_start_ns:
                pending = intents
                lag_left = int(signal_lag_bars)

    # flatten at last close
    if last_row:
        ts = last_row[next(iter(last_row))]["ts"]
        pxmap = {s: float(b["close"]) for s, b in last_row.items()}
        ledger.flatten_all(pxmap, ts, reason="eod")

    end_eq = float(ledger.equity())
    btc_bars = bar_map.get("BTC") or []
    bh = btc_buyhold_multiple(btc_bars, trade_start_ns)
    multiple = end_eq / float(start_usd) if start_usd else 0.0
    vs_btc = multiple / bh["btc_multiple"] if bh["btc_multiple"] else 0.0
    return {
        "strategy_id": getattr(strategy, "strategy_id", type(strategy).__name__),
        "account_type": "MARGIN_OVERNIGHT_4X_CAP",
        "symbols": [s.upper() for s in symbols],
        "start": start,
        "end": end,
        "start_usd": float(start_usd),
        "end_equity": end_eq,
        "multiple": multiple,
        "return_pct": (multiple - 1.0) * 100.0,
        "max_dd": _max_dd(curve),
        "fees_paid": ledger.fees_paid,
        "funding_paid": ledger.funding_paid,
        "liquidations": ledger.liquidations,
        "stop_exits": ledger.stop_exits,
        "trades": ledger.trades,
        "wins": ledger.wins,
        "losses": ledger.losses,
        "n_bars": n_bars,
        "curve_points": len(curve),
        "max_leverage": float(max_leverage),
        "fee_rate": float(fee_rate),
        "funding_per_8h": float(funding_per_8h),
        "funding_interval_hours": float(funding_interval_hours),
        "integer_contracts": bool(integer_contracts),
        "tape": tape,
        "price_proxy": "cde_1h_to_4h" if tape == "cde" else "spot_5m_to_4h",
        "btc_multiple": bh["btc_multiple"],
        "btc_start": bh["btc_start"],
        "btc_end": bh["btc_end"],
        "vs_btc_multiple": vs_btc,
        "hit_10x_btc_multiple": vs_btc >= 10.0,
        "hit_10x_btc_return": (multiple - 1.0) >= 10.0 * (bh["btc_multiple"] - 1.0),
        "flips": int(getattr(strategy, "flips", 0) or 0),
        "weeks": int(getattr(strategy, "weeks", 0) or 0),
        "signal_lag_bars": int(signal_lag_bars),
        "assumptions": [
            "spot 5m→4h proxy; BIP/ETP/SLP/XPP basis unmodeled",
            "overnight IM 25% (4x) cap; 10x intraday not used",
            "funding constant debit both sides, never credited",
            "next-open fills; intra-bar liq and stops on high/low",
            "integer nano lots",
        ],
    }


def write_json(obj: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, default=str) + "\n")
    return p
