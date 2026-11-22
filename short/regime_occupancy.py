#!/usr/bin/env python3
"""Occupancy of candidate BEAR definitions on BTC 4h. Research only."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from short.replay import load_4h_bars  # noqa: E402

WINDOWS = [
    ("2021-11-01", "2022-12-31", "bear2022"),
    ("2023-07-18", "2026-07-18", "long_window"),
    ("2024-03-01", "2024-09-01", "dd_2024"),
    ("2025-10-01", "2026-07-15", "dd_2025_26"),
    ("2020-08-01", "2026-08-13", "full"),
]


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).date().isoformat()


def build() -> pd.DataFrame:
    bars = load_4h_bars("BTC", start="2019-01-01", end="2026-08-13")
    df = pd.DataFrame(bars)
    df["dt"] = pd.to_datetime(df["ts"], unit="ns", utc=True)
    df = df.set_index("dt").sort_index()
    c = df["close"]
    # 4h 200 EMA (current DNA)
    df["ema200_4h"] = c.ewm(span=200, adjust=False).mean()
    df["bear_4h_ema"] = c < df["ema200_4h"]
    # daily 200 SMA of daily closes, forward-filled onto 4h
    daily = c.resample("1D").last().dropna()
    d200 = daily.rolling(200, min_periods=200).mean()
    df["sma200_d"] = d200.reindex(df.index, method="ffill")
    df["bear_d200"] = c < df["sma200_d"]
    # weekly 50 / 200 SMA
    weekly = c.resample("W-SUN").last().dropna()
    w50 = weekly.rolling(50, min_periods=50).mean()
    w200 = weekly.rolling(200, min_periods=200).mean()
    df["sma50_w"] = w50.reindex(df.index, method="ffill")
    df["sma200_w"] = w200.reindex(df.index, method="ffill")
    df["bear_w50"] = c < df["sma50_w"]
    df["bear_w200"] = c < df["sma200_w"]
    # TSMOM 3m / 12m (approx 90d / 365d on daily)
    df["ret_3m"] = c / c.shift(6 * 90) - 1.0
    df["ret_12m"] = c / c.shift(6 * 365) - 1.0
    df["bear_tsmom3"] = df["ret_3m"] < 0
    df["bear_tsmom12"] = df["ret_12m"] < 0
    df["bear_tsmom_and"] = (df["ret_3m"] < 0) & (df["ret_12m"] < 0)
    # drawdown from rolling 1y high / all-time high in sample
    roll_1y = c.rolling(6 * 365, min_periods=6 * 90).max()
    ath = c.cummax()
    df["dd_1y"] = c / roll_1y - 1.0
    df["dd_ath"] = c / ath - 1.0
    df["bear_dd30"] = df["dd_ath"] <= -0.30
    df["bear_dd50"] = df["dd_ath"] <= -0.50
    # Donchian 55-day (330 4h bars) breakdown state: close below prior 55d low
    look = 6 * 55
    prior_low = df["low"].shift(1).rolling(look, min_periods=look).min()
    df["donch_break"] = c < prior_low
    # persist in donchian downtrend: last break more recent than last 55d high break
    prior_high = df["high"].shift(1).rolling(look, min_periods=look).max()
    df["donch_up"] = c > prior_high
    state = np.zeros(len(df), dtype=bool)
    on = False
    for i, (dn, up) in enumerate(zip(df["donch_break"].fillna(False), df["donch_up"].fillna(False))):
        if dn:
            on = True
        elif up:
            on = False
        state[i] = on
    df["bear_donch55"] = state
    return df


GATES = [
    "bear_4h_ema",
    "bear_d200",
    "bear_w50",
    "bear_w200",
    "bear_tsmom3",
    "bear_tsmom12",
    "bear_tsmom_and",
    "bear_dd30",
    "bear_dd50",
    "bear_donch55",
]


def flips(s: pd.Series) -> int:
    v = s.astype(int).to_numpy()
    return int(np.sum(np.diff(v) != 0))


def score_window(df: pd.DataFrame, start: str, end: str) -> dict:
    sl = df.loc[start:end].copy()
    if sl.empty:
        return {}
    first, last = float(sl["close"].iloc[0]), float(sl["close"].iloc[-1])
    btc = last / first - 1.0
    out = {
        "n": int(len(sl)),
        "btc_ret": round(btc * 100, 2),
    }
    for g in GATES:
        mask = sl[g].fillna(False)
        frac = float(mask.mean())
        # BTC path while flagged bear vs not (product of bar rets)
        r = sl["close"].pct_change().fillna(0.0)
        bear_r = float((1.0 + r[mask]).prod() - 1.0) if mask.any() else 0.0
        bull_r = float((1.0 + r[~mask]).prod() - 1.0) if (~mask).any() else 0.0
        out[g] = {
            "frac": round(frac, 3),
            "flips": flips(mask),
            "btc_in": round(bear_r * 100, 1),
            "btc_out": round(bull_r * 100, 1),
        }
    return out


def main() -> int:
    df = build()
    print(f"BTC 4h bars {df.index.min().date()} → {df.index.max().date()} n={len(df)}")
    print()
    payload = {}
    header = f"{'window':14} {'btc':7} " + " ".join(f"{g.replace('bear_',''):>10}" for g in GATES)
    print(header)
    print("frac / flips / btc_while_in")
    for start, end, label in WINDOWS:
        row = score_window(df, start, end)
        payload[label] = row
        bits = [f"{label:14} {row['btc_ret']:+6.1f}%"]
        for g in GATES:
            d = row[g]
            bits.append(f"{d['frac']:.0%}/{d['flips']}/{d['btc_in']:+.0f}")
        print(" ".join(f"{b:>10}" if i else b for i, b in enumerate(bits)))
    out = ROOT / "results" / "backtests" / "short" / "regime_occupancy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # json-friendly
    import json

    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
