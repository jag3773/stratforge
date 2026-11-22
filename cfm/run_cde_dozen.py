#!/usr/bin/env python3
"""Dozen-plus distinct identities on CDE BIP (and ETH where needed)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cfm.replay import run_replay, write_json
from cfm.strats import DonchianTurtle, Hold1x, WeeklySma
from cfm.strats_cde import (
    AtrVolBreak,
    BollBreak,
    BollMeanRev,
    ChandelierLong,
    ConnorsRsi2,
    DonchianWeeks,
    DualBtcEthMom,
    HullTrend,
    IchimokuTJ,
    KeltnerBreak,
    MacdCross,
    RocWeeks,
    SeasonNovApr,
    Supertrend,
    TripleWeeklyMA,
    TsmomWeeks,
)

OUT = ROOT / "results" / "backtests" / "cfm" / "cde_dozen"
START, END = "2025-07-19", "2026-08-27"


def run(strat, symbols=None, start_usd=1000.0):
    r = run_replay(
        strat,
        symbols=symbols or ["BTC"],
        start=START,
        end=END,
        start_usd=start_usd,
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
        "mult": round(r["multiple"], 3),
        "vs_bip": round(r["vs_btc_multiple"], 3),
        "bip_m": round(r["btc_multiple"], 3),
        "dd": round(r["max_dd"] * 100, 1),
        "liq": r["liquidations"],
        "trades": r["trades"],
        "flips": r.get("flips", 0),
        "fund": round(r["funding_paid"], 1),
        "end_eq": round(r["end_equity"], 2),
        "usd": start_usd,
    }
    print(
        f"{rec['id'][:56]:56} vs={rec['vs_bip']:5.2f} mult={rec['mult']:5.2f} "
        f"dd={rec['dd']:5.1f}% liq={rec['liq']} tr={rec['trades']:3d} flips={rec['flips']:3d}",
        flush=True,
    )
    write_json(r, OUT / f"{r['strategy_id']}.json")
    return rec


def main():
    rows = []
    print("=== yardstick + frozen ===", flush=True)
    rows.append(run(Hold1x(["BTC"]), start_usd=2500))
    rows.append(run(WeeklySma(["BTC"], 40, 3.0, False, True)))
    print("=== 16 distinct families ===", flush=True)
    specs = [
        TsmomWeeks(13, 3.0, False),
        TsmomWeeks(26, 3.0, False),
        TsmomWeeks(13, 3.0, True),
        TripleWeeklyMA(3.0),
        DonchianWeeks(20, 10, 3.0),
        RocWeeks(12, 3.0),
        SeasonNovApr(2.0),
        IchimokuTJ(54, 156, 3.0),
        Supertrend(10, 3.0, 3.0),
        BollBreak(20, 2.0, 3.0),
        BollMeanRev(20, 2.0, 50, 2.0),
        ConnorsRsi2(2.0),
        HullTrend(32, 3.0),
        MacdCross(12, 26, 9, 3.0),
        KeltnerBreak(20, 20, 2.0, 3.0),
        ChandelierLong(22, 22, 3.0, 3.0),
        DualBtcEthMom(12, 1.5),
        AtrVolBreak(14, 1.5, 2.0, 12),
        DonchianTurtle(["BTC"], 330, 120, 120, 0.02, 2.0, 4.0, False),
    ]
    for s in specs:
        syms = ["BTC", "ETH"] if "btc_eth" in getattr(s, "strategy_id", "") else ["BTC"]
        rows.append(run(s, symbols=syms))
    write_json({"tape": "cde", "start": START, "end": END, "rows": rows}, OUT / "summary.json")
    print("\n=== rank vs BIP ===")
    for r in sorted(rows, key=lambda x: -x["vs_bip"]):
        print(f"  {r['vs_bip']:5.2f}xBIP {r['mult']:5.2f}x dd={r['dd']:5.1f}% liq={r['liq']} tr={r['trades']:3d}  {r['id']}")


if __name__ == "__main__":
    main()
