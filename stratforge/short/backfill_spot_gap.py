#!/usr/bin/env python3
"""Fill interior holes in a 5m parquet (BTC 2020-07 → 2023-07). Does not touch the long downloader."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import ccxt
import pandas as pd

from stratforge.state import state_root

RAW = state_root() / "data" / "raw"
STEP_MS = 5 * 60 * 1000
SLEEP = 0.25


def safe_fetch(ex, pair: str, since: int):
    delay = 3
    for _ in range(5):
        try:
            return ex.fetch_ohlcv(pair, "5m", since=since, limit=300)
        except ccxt.RateLimitExceeded:
            time.sleep(delay)
            delay = min(delay + 2, 12)
        except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.ExchangeNotAvailable):
            time.sleep(8)
    return None


def fill_symbol(sym: str, gap_start_ms: int, gap_end_ms: int) -> int:
    path = RAW / f"{sym}_USD_5m.parquet"
    old = pd.read_parquet(path) if path.is_file() else pd.DataFrame()
    have = set(int(t) for t in old["timestamp"].tolist()) if not old.empty else set()
    ex = ccxt.coinbase({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    pair = f"{sym}/USD"
    since = int(gap_start_ms)
    rows: list = []
    added = 0
    print(f"[{sym}] fill {pd.to_datetime(gap_start_ms, unit='ms')} → {pd.to_datetime(gap_end_ms, unit='ms')}", flush=True)
    while since < gap_end_ms:
        data = safe_fetch(ex, pair, since)
        if not data:
            since += 30 * 24 * 60 * 60 * 1000
            time.sleep(SLEEP)
            continue
        new = [r for r in data if r[0] not in have and gap_start_ms <= r[0] < gap_end_ms]
        rows.extend(new)
        have.update(r[0] for r in data)
        since = data[-1][0] + STEP_MS
        if len(rows) >= 12000:
            added += _commit(path, old if added == 0 and rows else None, rows, first=(added == 0))
            rows = []
            print(f"[{sym}] checkpoint since={pd.to_datetime(since, unit='ms')} added~{added}", flush=True)
        time.sleep(SLEEP)
    if rows:
        added += _commit(path, old if added == 0 else None, rows, first=(added == 0))
    print(f"[{sym}] done added_rows={added}", flush=True)
    return added


def _commit(path: Path, old: pd.DataFrame | None, rows: list, first: bool) -> int:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if first and old is not None and not old.empty:
        df = pd.concat([old, df], ignore_index=True)
    elif path.is_file() and not first:
        prev = pd.read_parquet(path)
        df = pd.concat([prev, df], ignore_index=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    df.to_parquet(path, index=False)
    return len(rows)


def main() -> int:
    sym = os.environ.get("COINS", "BTC").split(",")[0].strip().upper()
    start = os.environ.get("GAP_START", "2020-07-30")
    end = os.environ.get("GAP_END", "2023-07-18")
    a = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    b = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    fill_symbol(sym, a, b)
    df = pd.read_parquet(RAW / f"{sym}_USD_5m.parquet")
    ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    print("years", ts.dt.year.value_counts().sort_index().to_dict(), "rows", len(df), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
