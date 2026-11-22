#!/usr/bin/env python3
"""Battery of isolated CFM research identities vs BTC buy-and-hold.

Literature params frozen before looking at 3y scores. Not trend_rotation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cfm.replay import run_replay, write_json
from cfm.strats import DonchianTurtle, EmaRegimeLev, Hold1x, Tsmom12, VolTargetLong

OUT = ROOT / "results" / "backtests" / "cfm"

WINDOWS = {
    "3y": ("2023-08-28", "2026-08-27"),
    "2022": ("2022-01-01", "2022-12-31"),
    "full": ("2021-01-01", "2026-08-27"),
}


def specs() -> list[tuple[object, list[str], float]]:
    """(strategy, symbols, max_leverage_cap)."""
    out: list[tuple[object, list[str], float]] = []
    out.append((Hold1x(["BTC"]), ["BTC"], 4.0))
    for lev in (1.0, 2.0, 3.0, 4.0):
        out.append((Tsmom12(["BTC"], leverage=lev, short_enabled=True), ["BTC"], 4.0))
        out.append((Tsmom12(["BTC"], leverage=lev, short_enabled=False), ["BTC"], 4.0))
    out.append((Tsmom12(["BTC", "ETH"], leverage=3.0, short_enabled=True), ["BTC", "ETH"], 4.0))
    out.append((Tsmom12(["BTC", "ETH", "SOL", "XRP"], leverage=3.0, short_enabled=True), ["BTC", "ETH", "SOL", "XRP"], 4.0))
    out.append((Tsmom12(["BTC", "ETH", "SOL", "XRP"], leverage=4.0, short_enabled=True), ["BTC", "ETH", "SOL", "XRP"], 4.0))
    out.append((Tsmom12(["SOL"], leverage=3.0, short_enabled=True), ["SOL"], 4.0))
    # 55d/20d turtle on 4h = 330/120
    out.append((DonchianTurtle(["BTC"], 330, 120, 120, 0.02, 2.0, 4.0, True), ["BTC"], 4.0))
    out.append((DonchianTurtle(["BTC"], 330, 120, 120, 0.02, 2.0, 4.0, False), ["BTC"], 4.0))
    out.append((DonchianTurtle(["BTC", "ETH", "SOL"], 330, 120, 120, 0.02, 2.0, 4.0, True), ["BTC", "ETH", "SOL"], 4.0))
    # faster 20d/10d turtle (still literature)
    out.append((DonchianTurtle(["BTC"], 120, 60, 60, 0.02, 2.0, 4.0, True), ["BTC"], 4.0))
    for lev in (2.0, 3.0, 4.0):
        out.append((EmaRegimeLev(["BTC"], leverage=lev, short_enabled=True), ["BTC"], 4.0))
        out.append((EmaRegimeLev(["BTC"], leverage=lev, short_enabled=False), ["BTC"], 4.0))
    out.append((EmaRegimeLev(["BTC", "ETH", "SOL"], leverage=3.0, short_enabled=True), ["BTC", "ETH", "SOL"], 4.0))
    for tv in (0.40, 0.80, 1.20):
        out.append((VolTargetLong(["BTC"], target_vol=tv), ["BTC"], 4.0))
    return out


def keys(r: dict) -> dict:
    return {
        "strategy_id": r["strategy_id"],
        "window": f"{r['start']}..{r['end']}",
        "start_usd": r["start_usd"],
        "end_equity": round(r["end_equity"], 2),
        "multiple": round(r["multiple"], 3),
        "return_pct": round(r["return_pct"], 1),
        "max_dd": round(r["max_dd"] * 100, 1),
        "btc_multiple": round(r["btc_multiple"], 3),
        "vs_btc": round(r["vs_btc_multiple"], 3),
        "hit_10x_mult": r["hit_10x_btc_multiple"],
        "hit_10x_ret": r["hit_10x_btc_return"],
        "liq": r["liquidations"],
        "trades": r["trades"],
        "fees": round(r["fees_paid"], 2),
        "funding": round(r["funding_paid"], 2),
    }


def main() -> None:
    start_usd = 1000.0
    rows = []
    for wname, (a, b) in WINDOWS.items():
        for strat, syms, cap in specs():
            r = run_replay(
                strat,
                symbols=syms,
                start=a,
                end=b,
                start_usd=start_usd,
                max_leverage=cap,
                warmup_days=420,
            )
            r["window_name"] = wname
            rec = keys(r)
            rec["window_name"] = wname
            rec["symbols"] = ",".join(syms)
            rows.append(rec)
            print(
                f"{wname:5} {rec['strategy_id'][:56]:56} vsBTC={rec['vs_btc']:7.2f} "
                f"mult={rec['multiple']:7.2f} dd={rec['max_dd']:5.1f}% liq={rec['liq']} "
                f"hit10m={rec['hit_10x_mult']}",
                flush=True,
            )
            write_json(r, OUT / wname / f"{r['strategy_id']}.json")
    write_json({"start_usd": start_usd, "rows": rows}, OUT / "battery.json")
    # 3y ranking
    y3 = [x for x in rows if x["window_name"] == "3y"]
    y3.sort(key=lambda x: -x["vs_btc"])
    print("\n=== 3y rank by vs BTC multiple ===")
    for x in y3[:15]:
        print(f"  {x['vs_btc']:7.2f}xBTC  {x['multiple']:7.2f}x  dd={x['max_dd']:5.1f}%  {x['strategy_id']}")


if __name__ == "__main__":
    main()
