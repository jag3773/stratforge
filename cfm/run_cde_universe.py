#!/usr/bin/env python3
"""CDE full-universe CFM identities vs same-window spot SSOT.

SSOT bar (CASH trend_rotation_25_max8_rotate, $1000, 2025-07-19→2026-08-27): $1219.68 (+22%).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cfm.bars import load_cde_4h
from cfm.replay import run_replay, write_json
from cfm.strats import WeeklySma
from cfm.strats_cde import CrossSectionMom, TsmomCapLong
from cfm.venue import cde_symbols

OUT = ROOT / "results" / "backtests" / "cfm" / "cde_universe"
START, END = "2025-07-19", "2026-08-27"
SSOT_END = 1219.68


def coin_bh():
    print("=== CDE buy-hold (first→last 4h close) ===")
    rows = []
    for s in cde_symbols():
        b = load_cde_4h(s)
        if len(b) < 10:
            print(s, "too short", len(b))
            continue
        a, z = b[0]["close"], b[-1]["close"]
        rows.append((z / a, s, a, z, len(b)))
    rows.sort(reverse=True)
    for m, s, a, z, n in rows:
        print(f"  {s:6} {m:6.2f}x  {a:g} -> {z:g}  bars={n}")
    return rows


def go(strat, symbols, usd):
    r = run_replay(
        strat,
        symbols=symbols,
        start=START,
        end=END,
        start_usd=usd,
        max_leverage=4.0,
        fee_rate=0.0004,
        funding_per_8h=0.000012,
        funding_interval_hours=1.0,
        integer_contracts=True,
        warmup_days=1,
        tape="cde",
    )
    rec = {
        "id": r["strategy_id"],
        "usd": usd,
        "end_eq": round(r["end_equity"], 2),
        "mult": round(r["multiple"], 3),
        "vs_bip": round(r["vs_btc_multiple"], 3),
        "dd": round(r["max_dd"] * 100, 1),
        "liq": r["liquidations"],
        "trades": r["trades"],
        "flips": r.get("flips", 0),
        "fund": round(r["funding_paid"], 1),
        "beats_ssot_mult": r["multiple"] > 1.21968,
        "ssot_1000_scaled": round(1.21968 * usd, 2),
    }
    # compare dollars at same start
    rec["vs_ssot_$"] = round(rec["end_eq"] - rec["ssot_1000_scaled"], 2)
    print(
        f"{rec['id'][:52]:52} ${usd:.0f}->${rec['end_eq']:.0f}  {rec['mult']:.2f}x  "
        f"dd={rec['dd']:.0f}% liq={rec['liq']} tr={rec['trades']}  vsSSOT${rec['vs_ssot_$']:+.0f}",
        flush=True,
    )
    write_json(r, OUT / f"{r['strategy_id']}_usd{int(usd)}.json")
    return rec


def main():
    coin_bh()
    syms = cde_symbols()
    print("\n=== strategies (CDE tape, hourly funding) ===", flush=True)
    print(f"SSOT same window $1000 -> $1219.68  (scale linearly for other starts)", flush=True)
    rows = []
    for usd in (1000.0, 5000.0):
        specs = [
            CrossSectionMom(syms, 12, 4, 0, 0.5),
            CrossSectionMom(syms, 12, 4, 4, 0.4),
            CrossSectionMom(syms, 8, 6, 0, 0.4),
            CrossSectionMom(syms, 8, 4, 4, 0.4),
            TsmomCapLong(syms, 12, 8, 0.375),
            TsmomCapLong(syms, 8, 8, 0.375),
            TsmomCapLong(syms, 12, 4, 0.6),
            WeeklySma(["BTC"], 40, 3.0, False, True),
        ]
        for sp in specs:
            sy = ["BTC"] if "wsma40" in getattr(sp, "strategy_id", "") else syms
            rows.append(go(sp, sy, usd))
    write_json({"ssot": {"start": 1000, "end": SSOT_END, "mult": 1.21968}, "rows": rows}, OUT / "summary.json")
    print("\n=== beat SSOT multiple 1.22x ===")
    hits = [r for r in rows if r["beats_ssot_mult"]]
    if not hits:
        print("  none")
    for r in sorted(hits, key=lambda x: -x["mult"]):
        print(f"  {r['mult']:.2f}x  ${r['usd']:.0f}->${r['end_eq']:.0f}  {r['id']}")


if __name__ == "__main__":
    main()
