#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG))
from stratforge.state import state_root  # noqa: E402

from cfm.replay import run_replay, write_json
from cfm.strats import DonchianTurtle, MonthlySma, WeeklySma

OUT = state_root() / "results" / "backtests" / "cfm"


def specs():
    s = []
    for n in (10, 40):
        for lev in (2.0, 3.0):
            for hc in (True, False):
                for sh in (False, True):
                    s.append((WeeklySma(["BTC"], n=n, leverage=lev, short_enabled=sh, hold_contracts=hc), ["BTC"]))
            s.append((WeeklySma(["BTC", "ETH", "SOL"], n=n, leverage=lev, short_enabled=False, hold_contracts=hc), ["BTC", "ETH", "SOL"]))
    for lev in (2.0, 3.0):
        for hc in (True, False):
            s.append((MonthlySma(["BTC"], n=10, leverage=lev, hold_contracts=hc), ["BTC"]))
            s.append((MonthlySma(["BTC", "ETH", "SOL"], n=10, leverage=lev, hold_contracts=hc), ["BTC", "ETH", "SOL"]))
    s.append((DonchianTurtle(["BTC", "ETH", "SOL"], 330, 120, 120, 0.05, 2.0, 4.0, True), ["BTC", "ETH", "SOL"]))
    s.append((DonchianTurtle(["SOL"], 330, 120, 120, 0.02, 2.0, 4.0, True), ["SOL"]))
    return s


def main():
    windows = {
        "3y": ("2023-08-28", "2026-08-27"),
        "2022": ("2022-01-01", "2022-12-31"),
        "full": ("2021-01-01", "2026-08-27"),
        "y20_23": ("2020-08-28", "2023-08-27"),
        "y21_24": ("2021-08-28", "2024-08-27"),
        "y22_25": ("2022-08-28", "2025-08-27"),
    }
    rows = []
    for wname, (a, b) in windows.items():
        for strat, syms in specs():
            r = run_replay(strat, symbols=syms, start=a, end=b, start_usd=1000.0, max_leverage=4.0, warmup_days=420)
            rec = {
                "window": wname,
                "id": r["strategy_id"],
                "mult": round(r["multiple"], 3),
                "vs_btc": round(r["vs_btc_multiple"], 3),
                "dd": round(r["max_dd"] * 100, 1),
                "liq": r["liquidations"],
                "trades": r["trades"],
                "hit10m": r["hit_10x_btc_multiple"],
                "btc_m": round(r["btc_multiple"], 3),
            }
            rows.append(rec)
            print(
                f"{wname:7} vsBTC={rec['vs_btc']:6.2f} mult={rec['mult']:7.2f} dd={rec['dd']:5.1f}% liq={rec['liq']} {rec['id'][:64]}",
                flush=True,
            )
            write_json(r, OUT / "round2" / wname / f"{r['strategy_id']}.json")
    write_json({"rows": rows}, OUT / "round2.json")
    print("\n=== 3y rank ===")
    y3 = [x for x in rows if x["window"] == "3y"]
    y3.sort(key=lambda x: -x["vs_btc"])
    for x in y3[:12]:
        print(f"  {x['vs_btc']:6.2f}xBTC {x['mult']:6.2f}x dd={x['dd']:5.1f}% {x['id']}")
    print("\n=== hits 10x BTC multiple any window ===")
    hits = [x for x in rows if x["hit10m"]]
    if not hits:
        print("  none")
    for x in hits:
        print(x)


if __name__ == "__main__":
    main()
