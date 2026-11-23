#!/usr/bin/env python3
"""
Reliable recent 5m top-up for Coinbase raw data (keeps seed fresh for live node).

- Loops to catch up large gaps (repeated fetches from last known).
- Uses ccxt (confirmed to deliver fresh data).
- Dynamic symbols from live UNIVERSE (or COINS= env).
- Only appends truly new rows, dedups, checkpoints.
- Rate-limit aware.
- Suitable for cron (idempotent, fast once current).

Run:
  COINS=BTC,ETH python scripts/refresh_recent_5m.py
  # or full UNIVERSE
"""

import os
import sys
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import stratforge  # noqa: E402
from strategies.display_time import format_et  # noqa: E402
RAW_DIR = os.path.join(ROOT, "data", "raw")
TF = "5m"
STEP_MS = 5 * 60 * 1000
SLEEP = 0.4
MAX_PER_FETCH = 500   # safe chunk
MAX_ITERS_PER_COIN = 50  # safety for very large gaps

def get_symbols():
    if os.environ.get("COINS"):
        return [s.strip().upper() for s in os.environ["COINS"].split(",") if s.strip()]
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from strategies.universe import load_live_universe
        return list(load_live_universe())
    except Exception:
        uni = os.path.join(ROOT, "config", "universe_live.json")
        try:
            import json
            data = json.load(open(uni))
            symbols = data.get("symbols") or data.get("universe") or data
            if isinstance(symbols, dict):
                symbols = symbols.get("symbols") or []
            out = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if out:
                return out
        except Exception:
            pass
        print("ERROR: no symbol list (set COINS= or config/universe_live.json)", file=sys.stderr)
        sys.exit(1)

def load_last_ts(path: str) -> int | None:
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return None
        return int(df["timestamp"].max())
    except Exception:
        return None

def fetch_chunk(ex, pair: str, since_ms: int | None, retries: int = 2) -> list:
    """Try ccxt first (with retries). On persistent failure, fall back to direct public API."""
    for attempt in range(retries + 1):
        try:
            kw = {"limit": MAX_PER_FETCH}
            if since_ms:
                kw["since"] = since_ms
            data = ex.fetch_ohlcv(pair, TF, **kw)
            return data or []
        except ccxt.RateLimitExceeded:
            time.sleep(5)
            continue
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
            # Fall back to direct public API (same source as live feed, very reliable)
            try:
                sym = pair.split("/")[0]
                url = f"https://api.exchange.coinbase.com/products/{sym}-USD/candles"
                resp = requests.get(url, params={"granularity": 300}, timeout=15)
                if resp.status_code == 200:
                    raw = resp.json()
                    ohlcv = []
                    for row in raw[:MAX_PER_FETCH]:
                        # row: [unix_seconds, low, high, open, close, volume]
                        ts_ms = int(row[0]) * 1000
                        ohlcv.append([ts_ms, float(row[3]), float(row[2]), float(row[1]), float(row[4]), float(row[5])])
                    # sort ascending and filter to since
                    ohlcv.sort(key=lambda r: r[0])
                    if since_ms:
                        ohlcv = [r for r in ohlcv if r[0] >= since_ms]
                    return ohlcv
            except Exception:
                pass
            print(f"  [{pair}] fetch err {type(e).__name__} (ccxt+direct failed)", file=sys.stderr)
            time.sleep(3)
            return []
    return []

def append_new(path: str, new_rows: list) -> int:
    if not new_rows:
        return 0
    df_new = pd.DataFrame(new_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if os.path.exists(path):
        old = pd.read_parquet(path)
        combined = pd.concat([old, df_new])
    else:
        combined = df_new
    combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    combined.to_parquet(path)
    return len(df_new)

def main():
    symbols = get_symbols()
    ex = ccxt.coinbase({"enableRateLimit": True, "options": {"defaultType": "spot"}})

    updated = []
    no_change = []
    now_ms = int(time.time() * 1000)

    print(f"=== Recent 5m refresh for {len(symbols)} symbols @ {format_et(datetime.now(timezone.utc))} ===")

    for sym in symbols:
        pair = f"{sym}/USD"
        path = os.path.join(RAW_DIR, f"{sym}_USD_5m.parquet")
        last = load_last_ts(path)
        added_total = 0
        iters = 0

        while iters < MAX_ITERS_PER_COIN:
            iters += 1
            since = (last + STEP_MS) if last else None
            rows = fetch_chunk(ex, pair, since)
            if not rows:
                break

            new_rows = [r for r in rows if (not last or r[0] > last)]
            if not new_rows:
                break

            added = append_new(path, new_rows)
            if added > 0:
                added_total += added
                last = max(r[0] for r in new_rows)
                # continue to next chunk
            else:
                break

            time.sleep(SLEEP)

            # stop if we've reached very recent
            if last and (now_ms - last) < 6 * 3600 * 1000:  # within ~6h
                break

        if added_total > 0:
            last_dt = datetime.fromtimestamp(last / 1000, tz=timezone.utc) if last else None
            updated.append((sym, added_total, last_dt))
            print(f"[{sym}] +{added_total} (last ~{last_dt})")
        else:
            no_change.append(sym)

    print(f"\n=== Done: updated {len(updated)}, no-new {len(no_change)} ===")
    if updated:
        print("  Examples:", ", ".join(f"{s}(+{n})" for s, n, _ in updated[:8]) + ("..." if len(updated) > 8 else ""))
    print("Data should now be much fresher for seeds/warmup.")

if __name__ == "__main__":
    main()
