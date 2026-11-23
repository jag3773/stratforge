#!/usr/bin/env python3
"""Download Coinbase Advanced Trade public candles for CDE nanos (BIP/ETP/…).

Public GET /api/v3/brokerage/market/products/{id}/candles
Research only. No keys. Not a live adapter.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stratforge.state import state_root

UA = {"User-Agent": "pro-trader-cfm-research/1.0"}
PRODUCTS = {
    "BTC": "BIP-20DEC30-CDE",
    "ETH": "ETP-20DEC30-CDE",
    "SOL": "SLP-20DEC30-CDE",
    "XRP": "XPP-20DEC30-CDE",
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 + attempt * 3)
    return {}


def fetch_chunk(product_id: str, start: int, end: int, gran: str = "ONE_HOUR") -> list[dict]:
    url = (
        f"https://api.coinbase.com/api/v3/brokerage/market/products/"
        f"{urllib.parse.quote(product_id, safe='')}/candles"
        f"?start={int(start)}&end={int(end)}&granularity={gran}&limit=300"
    )
    js = _get(url)
    return list(js.get("candles") or [])


def download_hourly(symbol: str, product_id: str) -> Path:
    (state_root() / "data" / "cfm").mkdir(parents=True, exist_ok=True)
    path = (state_root() / "data" / "cfm") / f"{symbol}_CDE_1h.parquet"
    now = int(time.time())
    have_max = 0
    rows: list[dict] = []
    if path.is_file():
        old = pd.read_parquet(path)
        if not old.empty:
            have_max = int(old["timestamp"].max() // 1000)
            rows = old.to_dict("records")
            print(f"{symbol} resume after {datetime.fromtimestamp(have_max, timezone.utc)}")
    t = now
    empty_streak = 0
    while t > 1_700_000_000:  # 2023-ish floor; listing is 2025
        a = t - 12 * 86400
        if have_max and t <= have_max + 3600:
            break
        chunk = fetch_chunk(product_id, max(a, have_max + 1) if have_max else a, t)
        time.sleep(0.35)
        if not chunk:
            empty_streak += 1
            print(f"{symbol} empty {datetime.fromtimestamp(a, timezone.utc).date()}")
            if empty_streak >= 3:
                break
            t = a
            continue
        empty_streak = 0
        ts_list = [int(c["start"]) for c in chunk]
        print(
            f"{symbol} {datetime.fromtimestamp(min(ts_list), timezone.utc)} -> "
            f"{datetime.fromtimestamp(max(ts_list), timezone.utc)} n={len(chunk)}"
        )
        for c in chunk:
            rows.append(
                {
                    "timestamp": int(c["start"]) * 1000,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c["volume"]),
                    "product_id": product_id,
                }
            )
        t = min(ts_list)
        if len(chunk) < 50 and t < now - 86400:
            # listing edge
            more = fetch_chunk(product_id, t - 86400, t, "ONE_HOUR")
            if len(more) <= 1:
                break
    df = pd.DataFrame(rows)
    if df.empty:
        print(f"{symbol} no rows")
        return path
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    df.to_parquet(path, index=False)
    t0 = datetime.fromtimestamp(int(df["timestamp"].iloc[0]) / 1000, timezone.utc)
    t1 = datetime.fromtimestamp(int(df["timestamp"].iloc[-1]) / 1000, timezone.utc)
    print(f"wrote {path} rows={len(df)} {t0} -> {t1}")
    return path


def snapshot_venue() -> Path:
    out = (state_root() / "data" / "cfm") / "live_snapshot.json"
    snap: dict = {"ts_unix": int(time.time()), "products": {}}
    for sym, pid in PRODUCTS.items():
        js = _get(f"https://api.coinbase.com/api/v3/brokerage/market/products/{urllib.parse.quote(pid, safe='')}")
        fpd = js.get("future_product_details") or {}
        snap["products"][sym] = {
            "product_id": pid,
            "price": js.get("price"),
            "funding_rate": fpd.get("funding_rate"),
            "funding_interval": fpd.get("funding_interval"),
            "funding_time": fpd.get("funding_time"),
            "intraday_margin_rate": fpd.get("intraday_margin_rate"),
            "overnight_margin_rate": fpd.get("overnight_margin_rate"),
            "contract_size": fpd.get("contract_size"),
            "open_interest": fpd.get("open_interest"),
            "index_price": fpd.get("index_price"),
            "settlement_price": fpd.get("settlement_price"),
        }
        time.sleep(0.2)
    out.write_text(json.dumps(snap, indent=2) + "\n")
    print("wrote", out)
    return out


def _products() -> dict[str, str]:
    uni = state_root() / "config" / "cfm" / "universe_cde.json"
    if uni.is_file():
        raw = json.loads(uni.read_text())
        return {k: v["product_id"] for k, v in (raw.get("symbols") or {}).items()}
    return dict(PRODUCTS)


def main() -> None:
    products = _products()
    global PRODUCTS
    PRODUCTS = products
    snapshot_venue()
    for sym, pid in products.items():
        download_hourly(sym, pid)


if __name__ == "__main__":
    main()
