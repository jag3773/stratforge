#!/usr/bin/env python3
"""Replay CFM identities on CDE BIP 1h→4h tape (listing → now).

Venue-honest knobs: hourly funding 0.0012% debit, overnight cap 4x, 4 bps.
Not a 3y window. Not paper.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG))
from stratforge.state import state_root  # noqa: E402

from cfm.replay import run_replay, write_json
from cfm.strats import DonchianTurtle, EmaRegimeLev, Hold1x, MonthlySma, VolTargetLong, WeeklySma

OUT = state_root() / "results" / "backtests" / "cfm" / "cde_tape"
START = "2025-07-19"
END = "2026-08-27"
KW = dict(
    symbols=["BTC"],
    start=START,
    end=END,
    start_usd=1000.0,
    max_leverage=4.0,
    fee_rate=0.0004,
    funding_per_8h=0.000012,  # per interval
    funding_interval_hours=1.0,  # CDE hourly
    integer_contracts=True,
    warmup_days=1,
    tape="cde",
)


def go(strat) -> dict:
    r = run_replay(strat, **KW)
    rec = {
        "id": r["strategy_id"],
        "mult": round(r["multiple"], 3),
        "vs_bip": round(r["vs_btc_multiple"], 3),
        "bip_m": round(r["btc_multiple"], 3),
        "dd": round(r["max_dd"] * 100, 1),
        "liq": r["liquidations"],
        "trades": r["trades"],
        "flips": r.get("flips", 0),
        "fees": round(r["fees_paid"], 2),
        "funding": round(r["funding_paid"], 2),
        "end_eq": round(r["end_equity"], 2),
        "bip_start": round(r["btc_start"], 2),
        "bip_end": round(r["btc_end"], 2),
    }
    print(
        f"{rec['id'][:58]:58} vs={rec['vs_bip']:6.2f} mult={rec['mult']:6.2f} "
        f"dd={rec['dd']:5.1f}% liq={rec['liq']} tr={rec['trades']} fund={rec['funding']:.0f}",
        flush=True,
    )
    write_json(r, OUT / f"{r['strategy_id']}.json")
    return rec


def main() -> None:
    rows = []
    print("=== frozen + yardstick on CDE BIP ===", flush=True)
    rows.append(go(Hold1x(["BTC"])))
    rows.append(go(WeeklySma(["BTC"], n=40, leverage=3.0, short_enabled=False, hold_contracts=True)))
    rows.append(go(WeeklySma(["BTC"], n=40, leverage=3.0, short_enabled=True, hold_contracts=True)))
    print("=== shorter lookbacks / other DNA ===", flush=True)
    for n in (10, 20, 30):
        rows.append(go(WeeklySma(["BTC"], n=n, leverage=3.0, short_enabled=False, hold_contracts=True)))
        rows.append(go(WeeklySma(["BTC"], n=n, leverage=2.0, short_enabled=False, hold_contracts=True)))
    rows.append(go(WeeklySma(["BTC"], n=10, leverage=3.0, short_enabled=True, hold_contracts=True)))
    rows.append(go(WeeklySma(["BTC"], n=20, leverage=3.0, short_enabled=True, hold_contracts=True)))
    rows.append(go(MonthlySma(["BTC"], n=10, leverage=3.0, hold_contracts=True)))
    rows.append(go(DonchianTurtle(["BTC"], 330, 120, 120, 0.02, 2.0, 4.0, False)))
    rows.append(go(DonchianTurtle(["BTC"], 120, 60, 60, 0.02, 2.0, 4.0, False)))
    rows.append(go(EmaRegimeLev(["BTC"], leverage=2.0, fast=300, slow=1200, short_enabled=False)))
    rows.append(go(EmaRegimeLev(["BTC"], leverage=3.0, fast=75, slow=300, short_enabled=False)))  # ~12.5d/50d on 4h
    rows.append(go(VolTargetLong(["BTC"], target_vol=0.40)))
    rows.append(go(VolTargetLong(["BTC"], target_vol=0.80)))
    # 3x hold-contracts always-on = Hold1x but lev 3 via wsma that is always long? use Hold-like
    class HoldLev:
        def __init__(self, lev: float):
            self.strategy_id = f"cfm_bip_hold_lev{lev:g}"
            self._done = False
            self.lev = lev

        def on_close(self, ts, closes, highs, lows):
            if self._done or "BTC" not in closes:
                return []
            self._done = True
            from cfm.strats import Intent

            return [Intent("BTC", "LONG", self.lev, reason="hold", hold_contracts=True)]

    rows.append(go(HoldLev(3.0)))
    rows.append(go(HoldLev(2.0)))
    write_json({"start": START, "end": END, "tape": "cde", "funding": "0.000012/hour debit", "rows": rows}, OUT / "summary.json")
    rows_sorted = sorted(rows, key=lambda x: -x["vs_bip"])
    print("\n=== rank vs BIP buy-hold ===")
    for r in rows_sorted:
        print(f"  {r['vs_bip']:6.2f}xBIP  {r['mult']:6.2f}x  dd={r['dd']:5.1f}%  {r['id']}")


if __name__ == "__main__":
    main()
