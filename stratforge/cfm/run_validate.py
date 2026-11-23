#!/usr/bin/env python3
"""Stagger + stress for frozen wsma40 lev3 hold-contracts identities.

Not a spot stagger. Not paper. Overlapping births are sensitivity, not independent OOS.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG))
from stratforge.state import state_root  # noqa: E402

from cfm.replay import run_replay, write_json
from cfm.strats import WeeklySma

OUT = state_root() / "results" / "backtests" / "cfm" / "validate"
END = "2026-08-27"
PRIMARY = ("2023-08-28", END)


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def make(n: int = 40, lev: float = 3.0, ls: bool = False) -> WeeklySma:
    return WeeklySma(["BTC"], n=n, leverage=lev, short_enabled=ls, hold_contracts=True)


def compact(r: dict, extra: dict | None = None) -> dict:
    d = {
        "id": r["strategy_id"],
        "start": r["start"],
        "end": r["end"],
        "mult": round(r["multiple"], 3),
        "vs_btc": round(r["vs_btc_multiple"], 3),
        "btc_m": round(r["btc_multiple"], 3),
        "dd": round(r["max_dd"] * 100, 1),
        "liq": r["liquidations"],
        "trades": r["trades"],
        "flips": r.get("flips", 0),
        "fees": round(r["fees_paid"], 2),
        "funding": round(r["funding_paid"], 2),
        "end_eq": round(r["end_equity"], 2),
    }
    if extra:
        d.update(extra)
    return d


def run(ls: bool, start: str, end: str, **kw) -> dict:
    n = int(kw.pop("n", 40))
    lev = float(kw.pop("lev", 3.0))
    return run_replay(
        make(n=n, lev=lev, ls=ls),
        symbols=["BTC"],
        start=start,
        end=end,
        start_usd=float(kw.pop("start_usd", 1000)),
        max_leverage=4.0,
        fee_rate=float(kw.pop("fee_rate", 0.0004)),
        funding_per_8h=float(kw.pop("funding_per_8h", 0.00003)),
        integer_contracts=bool(kw.pop("integer_contracts", True)),
        warmup_days=400,
        signal_lag_bars=int(kw.pop("signal_lag_bars", 0)),
    )


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    vs = [r["vs_btc"] for r in rows]
    m = [r["mult"] for r in rows]
    dd = [r["dd"] for r in rows]
    liq = [r["liq"] for r in rows]
    return {
        "n": len(rows),
        "vs_med": round(statistics.median(vs), 3),
        "vs_avg": round(sum(vs) / len(vs), 3),
        "vs_min": round(min(vs), 3),
        "vs_max": round(max(vs), 3),
        "mult_med": round(statistics.median(m), 3),
        "mult_min": round(min(m), 3),
        "mult_max": round(max(m), 3),
        "dd_med": round(statistics.median(dd), 1),
        "dd_max": round(max(dd), 1),
        "beat_btc": int(sum(1 for x in vs if x > 1.0)),
        "liq_any": int(sum(1 for x in liq if x > 0)),
        "liq_med": statistics.median(liq),
        "worst": min(rows, key=lambda r: r["mult"]),
        "best": max(rows, key=lambda r: r["mult"]),
    }


def main() -> None:
    report: dict = {"primary": [], "years": [], "rolling_3y": [], "same_end": [], "stress": [], "robust_n": []}
    print("=== primary 3y (calendar-week SMA) ===", flush=True)
    for ls, name in ((False, "long"), (True, "ls")):
        r = compact(run(ls, *PRIMARY), {"name": name})
        report["primary"].append(r)
        print(f"  {name:4} vsBTC={r['vs_btc']:.2f} mult={r['mult']:.2f} dd={r['dd']:.1f}% liq={r['liq']} flips={r['flips']}", flush=True)

    print("=== calendar years ===", flush=True)
    years = [
        ("2021", "2021-01-01", "2021-12-31"),
        ("2022", "2022-01-01", "2022-12-31"),
        ("2023", "2023-01-01", "2023-12-31"),
        ("2024", "2024-01-01", "2024-12-31"),
        ("2025", "2025-01-01", "2025-12-31"),
        ("2026ytd", "2026-01-01", END),
    ]
    for lab, a, b in years:
        for ls, name in ((False, "long"), (True, "ls")):
            r = compact(run(ls, a, b), {"name": name, "year": lab})
            report["years"].append(r)
            print(f"  {lab:7} {name:4} vsBTC={r['vs_btc']:.2f} mult={r['mult']:.2f} dd={r['dd']:.1f}% liq={r['liq']}", flush=True)

    print("=== 3y rolling monthly births ===", flush=True)
    d0 = date(2020, 9, 1)
    d1 = date(2023, 8, 1)
    d = d0
    while d <= d1:
        a = d.isoformat()
        end_d = add_months(d, 36) - timedelta(days=1)
        end = min(end_d.isoformat(), END)
        for ls, name in ((False, "long"), (True, "ls")):
            r = compact(run(ls, a, end), {"name": name, "birth": a})
            report["rolling_3y"].append(r)
        print(f"  {a} long vs={report['rolling_3y'][-2]['vs_btc']:.2f} ls vs={report['rolling_3y'][-1]['vs_btc']:.2f} liq={report['rolling_3y'][-2]['liq']}/{report['rolling_3y'][-1]['liq']}", flush=True)
        d = add_months(d, 1)

    print("=== same-end length stagger (end 2026-08-27) ===", flush=True)
    for months in (6, 12, 18, 24, 30, 36):
        start = add_months(date(2026, 8, 1), -months).isoformat()
        for ls, name in ((False, "long"), (True, "ls")):
            r = compact(run(ls, start, END), {"name": name, "months": months})
            report["same_end"].append(r)
            print(f"  m{months:02d} {name:4} vsBTC={r['vs_btc']:.2f} mult={r['mult']:.2f} dd={r['dd']:.1f}% liq={r['liq']}", flush=True)

    print("=== stress on primary 3y ===", flush=True)
    stresses = [
        {"label": "fee2bps", "fee_rate": 0.0002},
        {"label": "fee4bps", "fee_rate": 0.0004},
        {"label": "fee8bps", "fee_rate": 0.0008},
        {"label": "fund_x3", "funding_per_8h": 0.00009},
        {"label": "fund_x5", "funding_per_8h": 0.00015},
        {"label": "usd500", "start_usd": 500},
        {"label": "usd10k", "start_usd": 10000},
        {"label": "no_integer", "integer_contracts": False},
        {"label": "lag1w", "signal_lag_bars": 42},
        {"label": "lev2", "lev": 2.0},
    ]
    for ls, name in ((False, "long"), (True, "ls")):
        for st in stresses:
            kw = {k: v for k, v in st.items() if k != "label"}
            r = compact(run(ls, *PRIMARY, **kw), {"name": name, "stress": st["label"]})
            report["stress"].append(r)
            print(f"  {name:4} {st['label']:12} vsBTC={r['vs_btc']:.2f} mult={r['mult']:.2f} dd={r['dd']:.1f}% liq={r['liq']}", flush=True)

    print("=== robustness n=30/40/50 lev=2/3 primary ===", flush=True)
    for n in (30, 40, 50):
        for lev in (2.0, 3.0):
            for ls, name in ((False, "long"), (True, "ls")):
                r = compact(run(ls, *PRIMARY, n=n, lev=lev), {"name": name, "n": n, "lev": lev})
                report["robust_n"].append(r)
                print(f"  n={n} L={lev:g} {name:4} vsBTC={r['vs_btc']:.2f} mult={r['mult']:.2f} dd={r['dd']:.1f}% liq={r['liq']}", flush=True)

    roll_long = [x for x in report["rolling_3y"] if x["name"] == "long"]
    roll_ls = [x for x in report["rolling_3y"] if x["name"] == "ls"]
    summary = {
        "rolling_3y_long": summarize(roll_long),
        "rolling_3y_ls": summarize(roll_ls),
        "same_end_long": summarize([x for x in report["same_end"] if x["name"] == "long"]),
        "same_end_ls": summarize([x for x in report["same_end"] if x["name"] == "ls"]),
    }
    # strip nested best/worst to ids
    for k, v in summary.items():
        if not v:
            continue
        v["worst"] = {"start": v["worst"]["start"], "mult": v["worst"]["mult"], "vs_btc": v["worst"]["vs_btc"], "dd": v["worst"]["dd"]}
        v["best"] = {"start": v["best"]["start"], "mult": v["best"]["mult"], "vs_btc": v["best"]["vs_btc"], "dd": v["best"]["dd"]}

    out = {"asof": END, "identities": ["cfm_wsma40_long_lev3_holdc_btc", "cfm_wsma40_ls_lev3_holdc_btc"], "rows": report, "summary": summary}
    write_json(out, OUT / "stagger_stress.json")
    print("\n=== ROLLING 3Y SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
