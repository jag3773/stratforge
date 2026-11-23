"""5m parquet → 4h proxy bars. Own fold. Do not import short.replay or live_data_feed."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd

from stratforge.state import state_root


def _raw_dir() -> Path:
    return state_root() / "data" / "raw"


def _cfm_dir() -> Path:
    return state_root() / "data" / "cfm"


def _frame_to_bars(df: pd.DataFrame, freq: str) -> list[dict]:
    out = pd.DataFrame(
        {
            "open": df["open"].resample(freq).first(),
            "high": df["high"].resample(freq).max(),
            "low": df["low"].resample(freq).min(),
            "close": df["close"].resample(freq).last(),
            "volume": df["volume"].resample(freq).sum(),
        }
    ).dropna()
    bars: list[dict] = []
    for t, row in out.iterrows():
        bars.append(
            {
                "ts": int(t.timestamp() * 1_000_000_000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return bars


@lru_cache(maxsize=16)
def load_cde_4h(symbol: str) -> list[dict]:
    path = _cfm_dir() / f"{symbol.upper()}_CDE_1h.parquet"
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    if df.empty:
        return []
    df = df.copy()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    return _frame_to_bars(df, "4h")


def _to_dt(s: str | datetime | None) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


@lru_cache(maxsize=16)
def load_4h_all(symbol: str, raw_dir: str = "") -> list[dict]:
    return load_4h_bars(symbol, None, None, raw_dir)


@lru_cache(maxsize=64)
def load_4h_bars(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    raw_dir: str = "",
) -> list[dict]:
    path = Path(raw_dir or _raw_dir()) / f"{symbol.upper()}_USD_5m.parquet"
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    if df.empty:
        return []
    df = df.copy()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    start_dt = _to_dt(start)
    end_dt = _to_dt(end)
    if end_dt is not None:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)
    if start_dt is not None:
        df = df[df["ts"] >= start_dt]
    if end_dt is not None:
        df = df[df["ts"] <= end_dt]
    if df.empty:
        return []
    df = df.set_index("ts").sort_index()
    out = pd.DataFrame(
        {
            "open": df["open"].resample("4h").first(),
            "high": df["high"].resample("4h").max(),
            "low": df["low"].resample("4h").min(),
            "close": df["close"].resample("4h").last(),
            "volume": df["volume"].resample("4h").sum(),
        }
    ).dropna()
    bars: list[dict] = []
    for t, row in out.iterrows():
        bars.append(
            {
                "ts": int(t.timestamp() * 1_000_000_000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return bars
