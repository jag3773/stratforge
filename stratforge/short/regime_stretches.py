#!/usr/bin/env python3
"""Print bear-gate stretches for the occupancy winners."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from short.regime_occupancy import build  # noqa: E402

GATES = ["bear_w50", "bear_tsmom12", "bear_dd30", "bear_d200"]
WINDOWS = [
    ("2021-11-01", "2022-12-31", "bear2022"),
    ("2023-07-18", "2026-07-18", "long_window"),
    ("2025-10-01", "2026-07-15", "crash"),
]


def main() -> int:
    df = build()
    if df.empty:
        print("no 4h bars")
        return 0
    for start, end, label in WINDOWS:
        sl = df.loc[start:end]
        if sl.empty or "close" not in sl.columns:
            print(f"\n=== {label} no bars ===")
            continue
        print(
            f"\n=== {label} BTC {sl.close.iloc[0]:.0f}->{sl.close.iloc[-1]:.0f} ==="
        )
        for g in GATES:
            s = sl[g].fillna(False)
            if not bool(s.any()):
                print(f"  {g}: NEVER ON")
                continue
            on = s.astype(int)
            grp = (on != on.shift()).cumsum()
            n = 0
            for _, chunk in sl.groupby(grp):
                if not bool(chunk[g].iloc[0]):
                    continue
                n += 1
                a = chunk.index[0].date()
                b = chunk.index[-1].date()
                pa = float(chunk.close.iloc[0])
                pb = float(chunk.close.iloc[-1])
                ret = (pb / pa - 1.0) * 100.0
                print(f"  {g}: {a} @ {pa:.0f} -> {b} @ {pb:.0f}  ({ret:+.0f}%, {len(chunk)} bars)")
            print(f"    stretches={n} frac={float(s.mean()):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
