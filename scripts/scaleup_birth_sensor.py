#!/usr/bin/env python3
"""Scale-up sensor: newest monthly births vs early-m10 *shape* (issue #7).

Read-only. Does **not** change DNA, paper, or LIVE.

Existing cohort JSONs are **end-of-window snapshots**, not intra-run curves.
The GO_LIVE “early m10” template (cash collapsing, open bags, win rate still
fine while equity is red) is scored against those snapshots. That is a
go / no-go for *adding size*, not a birth-date guarantee.

Production DNA SSOT: config/trend_rotation_25_max8_rotate.json
  max_alloc_pct=0.25, max_concurrent=8, rotate_when_full=true,
  rotate_min_hold_bars=14, CASH, entry_mode=breakout, exit_mode=slow_ema,
  atr_stop_mult=0.0. Overlapping monthly windows are sensitivity, not 12
  independent OOS proofs.

First verified max8+rotate cohort used hold=10; live DNA prefers 14.
The sensor reports knobs from each artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COHORT = ROOT / "data" / "staggered_starts" / "monthly_history_max8_rotate"
DNA_PATH = ROOT / "config" / "trend_rotation_25_max8_rotate.json"
M10_START = date(2025, 10, 2)

# GO_LIVE recipe hostile template (baseline m10 path), not max8 m10 *end*
# which recovered to ~+17% on this DNA.
DEEP_RED_PCT = -15.0
CASH_FRAC_MAX = 0.10
OPEN_BAGS_UPNL_PCT = -15.0
FULL_BOOK = 7
WR_FALSE_COMFORT = 20.0


def _f(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x, default=0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def load_dna(path: Path = DNA_PATH) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_birth_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    win = data.get("window") or {}
    start = win.get("start") or data.get("start")
    if not start:
        return None
    opens = data.get("open_positions") or []
    if opens:
        open_n = 0
        for pos in opens:
            mv = _f(pos.get("market_value"))
            if mv is None:
                qty = _f(pos.get("qty"), 0.0) or 0.0
                px = _f(pos.get("last_price"), 0.0) or 0.0
                mv = qty * px
            if abs(mv) >= 1.0:
                open_n += 1
    else:
        open_n = _i(data.get("open_n"))
    equity = _f(data.get("equity"), 0.0) or 0.0
    cash = _f(data.get("cash") or data.get("usd_cash"), 0.0) or 0.0
    start_bal = _f(data.get("start_balance"), 1000.0) or 1000.0
    upnl = _f(data.get("unrealized_pnl"))
    if upnl is None:
        mv = _f(data.get("positions_mv"))
        cost = _f(data.get("positions_cost"))
        if mv is not None and cost is not None:
            upnl = mv - cost
    cfg = data.get("config") or {}
    ret = _f(data.get("strategy_return_pct") or data.get("return_pct"))
    if ret is None and start_bal:
        ret = (equity / start_bal - 1.0) * 100.0
    return {
        "path": str(path),
        "start": start,
        "end": win.get("end") or data.get("end"),
        "equity": equity,
        "cash": cash,
        "start_balance": start_bal,
        "return_pct": ret if ret is not None else 0.0,
        "btc_return_pct": _f(data.get("btc_buyhold_return_pct") or data.get("btc_return_pct")),
        "beats_btc": bool(data.get("beats_btc_buyhold") if "beats_btc_buyhold" in data else data.get("beats_btc")),
        "closed_trades": _i(data.get("closed_trades")),
        "win_rate": _f(data.get("win_rate"), 0.0) or 0.0,
        "open_n": open_n,
        "open_upnl": upnl,
        "cash_frac": (cash / equity) if equity else None,
        "open_upnl_pct": (upnl / equity * 100.0) if upnl is not None and equity else None,
        "rotate_min_hold_bars": cfg.get("rotate_min_hold_bars"),
        "max_concurrent": cfg.get("max_concurrent"),
        "max_alloc_pct": cfg.get("max_alloc_pct"),
        "rotate_when_full": cfg.get("rotate_when_full"),
        "atr_stop_mult": cfg.get("atr_stop_mult"),
        "entry_mode": cfg.get("mode") or data.get("mode"),
        "exit_mode": cfg.get("exit_mode"),
        "config_path": data.get("config_path"),
        "account_type": data.get("account_type"),
    }


def load_cohort(cohort_dir: Path) -> list[dict]:
    rows: list[dict] = []
    if not cohort_dir.is_dir():
        return rows
    for path in sorted(cohort_dir.glob("*.json")):
        if path.name == "rollup.json":
            continue
        row = load_birth_file(path)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: r["start"])
    return rows


def m10_like_flags(row: dict) -> list[str]:
    """Hostile *shape* from GO_LIVE_RECIPE, not “equals 2025-10 end equity”."""
    flags: list[str] = []
    ret = _f(row.get("return_pct"), 0.0) or 0.0
    wr = _f(row.get("win_rate"), 0.0) or 0.0
    cash_frac = row.get("cash_frac")
    open_n = _i(row.get("open_n"))
    upnl_pct = row.get("open_upnl_pct")
    if ret <= DEEP_RED_PCT:
        flags.append("deep_red")
    if cash_frac is not None and cash_frac < CASH_FRAC_MAX:
        flags.append("cash_collapse")
    if open_n >= FULL_BOOK and upnl_pct is not None and upnl_pct <= OPEN_BAGS_UPNL_PCT:
        flags.append("open_bags")
    if ret < 0 and wr >= WR_FALSE_COMFORT:
        flags.append("wr_false_comfort")
    return flags


def is_m10_like(flags: list[str]) -> bool:
    return len(flags) >= 2 or (
        "deep_red" in flags and ("open_bags" in flags or "cash_collapse" in flags)
    )


def verdict(newest: list[dict]) -> str:
    """GO / PAUSE / NO-GO for *adding size only*."""
    if not newest:
        return "NO-DATA"
    hostile = [r for r in newest if is_m10_like(r.get("flags") or [])]
    if hostile:
        return "NO-GO"
    single = [r for r in newest if r.get("flags")]
    if single:
        return "PAUSE"
    return "GO"


def evaluate(cohort_dir: Path, *, newest_n: int = 3, m10_start: str = M10_START.isoformat()) -> dict:
    dna = load_dna()
    rows = load_cohort(cohort_dir)
    for r in rows:
        r["flags"] = m10_like_flags(r)
        r["m10_like"] = is_m10_like(r["flags"])
    newest = rows[-newest_n:] if rows else []
    m10 = next((r for r in rows if r["start"] == m10_start), None)
    return {
        "strategy_id": dna.get("strategy_id") or "trend_rotation_25_max8_rotate",
        "dna_file": str(DNA_PATH.relative_to(ROOT)) if DNA_PATH.is_file() else "config/trend_rotation_25_max8_rotate.json",
        "dna": {
            "max_alloc_pct": dna.get("max_alloc_pct", 0.25),
            "max_concurrent": dna.get("max_concurrent", 8),
            "rotate_when_full": dna.get("rotate_when_full", True),
            "rotate_min_hold_bars": dna.get("rotate_min_hold_bars", 14),
            "atr_stop_mult": dna.get("atr_stop_mult", 0.0),
            "entry_mode": dna.get("mode", "breakout"),
            "exit_mode": dna.get("exit_mode", "slow_ema"),
            "account_type": dna.get("account_type", "CASH"),
        },
        "cohort_dir": str(cohort_dir),
        "n_births": len(rows),
        "newest_n": newest_n,
        "m10_start": m10_start,
        "m10": m10,
        "newest": newest,
        "verdict": verdict(newest),
        "caveat": (
            "End-of-window snapshots only (no intra-run curve). "
            "Overlapping monthly windows = sensitivity, not independent OOS. "
            "Verdict is for adding size, not DNA."
        ),
    }


def _pct(x) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def format_report(ev: dict) -> str:
    d = ev["dna"]
    lines = [
        f"**Scale-up sensor** `{ev['strategy_id']}`",
        (
            f"file `{ev['dna_file']}` · max_alloc_pct={d['max_alloc_pct']} · "
            f"max_concurrent={d['max_concurrent']} · rotate_when_full={d['rotate_when_full']} · "
            f"rotate_min_hold_bars={d['rotate_min_hold_bars']} · atr_stop_mult={d['atr_stop_mult']} · "
            f"entry_mode={d['entry_mode']} · exit_mode={d['exit_mode']} · {d['account_type']}"
        ),
        f"cohort {ev['n_births']} births · newest {ev['newest_n']} vs m10 {ev['m10_start']}",
        f"**Verdict (add size only): {ev['verdict']}**",
        ev["caveat"],
    ]
    m10 = ev.get("m10")
    if m10:
        hold = m10.get("rotate_min_hold_bars")
        lines.append(
            f"m10 artifact end: ret {_pct(m10['return_pct'])} · cash/eq "
            f"{(m10['cash_frac'] * 100):.0f}% · open {m10['open_n']} · WR {m10['win_rate']:.1f}% · "
            f"BTC {_pct(m10['btc_return_pct'])} · hold={hold} "
            f"(hostile *template* is recipe shape, not this recovered end)"
        )
    else:
        lines.append(f"m10 {ev['m10_start']} not in cohort dir")
    lines.append("**Newest births**")
    if not ev["newest"]:
        lines.append("none")
    for r in ev["newest"]:
        flags = ",".join(r.get("flags") or []) or "none"
        mark = "m10-like" if r.get("m10_like") else "ok"
        cf = f"{r['cash_frac'] * 100:.0f}%" if r.get("cash_frac") is not None else "n/a"
        up = _pct(r.get("open_upnl_pct"))
        lines.append(
            f"- {r['start']}→{r['end']} {mark}: ret {_pct(r['return_pct'])} · "
            f"cash/eq {cf} · open {r['open_n']} · uPnL {up} · WR {r['win_rate']:.1f}% · "
            f"BTC {_pct(r['btc_return_pct'])} · flags {flags}"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Newest births vs early-m10 shape (add-size sensor)")
    ap.add_argument("--cohort", type=Path, default=DEFAULT_COHORT)
    ap.add_argument("--newest", type=int, default=3)
    ap.add_argument("--m10-start", default=M10_START.isoformat())
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    ev = evaluate(args.cohort, newest_n=args.newest, m10_start=args.m10_start)
    if args.json:
        print(json.dumps(ev, indent=2, default=str))
    else:
        print(format_report(ev))
    if ev["verdict"] == "NO-DATA":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
