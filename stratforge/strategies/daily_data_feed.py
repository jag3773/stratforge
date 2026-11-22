"""Isolated UTC-daily REST feed. Do not import from live_node.

Seed from parquet via research_replay.load_4h_bars(bar_minutes=1440).
Poll public 5m candles, fold to completed UTC days, emit once per bucket.
Same-cycle closed days flush A–Z to match research_families stream sort.
Health file is caller-supplied (feed_health_donch.json) — never feed_health.json.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Callable

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POLL_SLEEP = float(os.getenv("FEED_POLL_SLEEP", "45"))
DAY_MS = 24 * 3600 * 1000
BAR_MINUTES = 1440


def _floor_day_ms(ts_ms: int) -> int:
    secs = int(ts_ms) // 1000
    return (secs // (24 * 3600)) * DAY_MS


def bucket_end_ms(bucket_ms: int) -> int:
    return int(bucket_ms) + DAY_MS


def bucket_is_closed(bucket_ms: int, now_ms: int | None = None) -> bool:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return now >= bucket_end_ms(bucket_ms)


def bucket_ms_from_bar_ts(ts: int) -> int:
    t = int(ts)
    if t > 10_000_000_000_000_000:
        ts_ms = t // 1_000_000
    elif t > 10_000_000_000:
        ts_ms = t
    else:
        ts_ms = t * 1000
    return _floor_day_ms(ts_ms)


def bar_ts_ns_from_bucket_ms(bucket_ms: int) -> int:
    return int(bucket_ms) * 1_000_000


def trim_unclosed_seed_bars(
    bars: list[dict], now_ms: int | None = None
) -> list[dict]:
    if not bars:
        return list(bars)
    out = list(bars)
    wall = int(time.time() * 1000) if now_ms is None else int(now_ms)
    while out:
        bkt = bucket_ms_from_bar_ts(out[-1]["ts"])
        if bucket_is_closed(bkt, wall):
            break
        out.pop()
    return out


class DailyDataFeed:
    def __init__(
        self,
        symbols: list[str],
        on_bar: Callable,
        history_end_iso: str | None = None,
        poll_sleep: float = POLL_SLEEP,
        health_path: str = "",
    ):
        if not health_path:
            raise ValueError("DailyDataFeed requires an isolated health_path")
        self.symbols = symbols
        self.on_bar = on_bar
        self.history_end_iso = history_end_iso
        self.poll_sleep = poll_sleep
        self.health_path = health_path
        self._cur: dict[str, dict] = {}
        self._seed_bars: dict[str, list[dict]] = {}
        self._last_emitted: dict[str, int] = {}
        self._pending: list[tuple[int, str, dict, str]] = []
        self._stop = False
        self._cycle = 0
        self._bars_emitted = 0
        self._fetch_ok = 0
        self._fetch_fail = 0
        self._last_bar_ts = None
        self._last_bar_symbol = None
        self._last_bar_bucket = None
        self._early_hold_logged: set[tuple[str, int]] = set()
        self._dedup_logged: set[tuple[str, int]] = set()

    def seed(self) -> dict[str, list[dict]]:
        from strategies.research_replay import load_4h_bars

        end = None
        if self.history_end_iso:
            end = self.history_end_iso[:10]
        for sym in self.symbols:
            bars = load_4h_bars(sym, end=end, bar_minutes=BAR_MINUTES)
            bars = trim_unclosed_seed_bars(bars)
            self._seed_bars[sym] = bars
            if bars:
                bkt = bucket_ms_from_bar_ts(bars[-1]["ts"])
                self._last_emitted[sym] = bkt
                self._note_closed_bucket(sym, bkt)
        return self._seed_bars

    def init_emitted_from_seed(
        self,
        seed_bars: dict[str, list[dict]] | None = None,
        now_ms: int | None = None,
    ):
        src = seed_bars if seed_bars is not None else self._seed_bars
        for sym, bars in src.items():
            closed = trim_unclosed_seed_bars(bars, now_ms=now_ms)
            if seed_bars is None:
                self._seed_bars[sym] = closed
            if closed:
                bkt = bucket_ms_from_bar_ts(closed[-1]["ts"])
                self._last_emitted[sym] = bkt
                self._note_closed_bucket(sym, bkt)
            else:
                self._last_emitted.pop(sym, None)

    def _note_closed_bucket(self, symbol: str, bucket_ms: int) -> None:
        """Remember a closed seed day without counting a live emit.

        Mid-day restart must not re-trade that day, and must not look like a
        missed close. Stamp the bucket *end* (UTC midnight it closed), not
        wall-clock now — a restart 18h later still ages against the real close.
        A later live emit owns the clock (`_bars_emitted` > 0).
        """
        if self._bars_emitted:
            return
        bkt = int(bucket_ms)
        prev = self._last_bar_bucket
        if prev is not None and int(prev) > bkt:
            return
        self._last_bar_bucket = bkt
        self._last_bar_symbol = symbol
        end = bucket_end_ms(bkt)
        self._last_bar_ts = datetime.fromtimestamp(end / 1000, timezone.utc).isoformat()

    def _queue(self, symbol: str, bar: dict, reason: str) -> None:
        bkt = bucket_ms_from_bar_ts(bar["ts"])
        prev = self._last_emitted.get(symbol, -1)
        if bkt <= prev:
            key = (symbol, int(bkt))
            if key not in self._dedup_logged:
                self._dedup_logged.add(key)
                print(
                    f"[feed] skip 1d {symbol} bucket={bkt} "
                    f"(already emitted prev={prev}) via={reason}",
                    flush=True,
                )
            return
        # Reserve the bucket so a later flush cannot double-queue.
        self._last_emitted[symbol] = bkt
        self._pending.append((bkt, symbol, bar, reason))

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = []
        pending.sort(key=lambda x: (x[0], x[1]))
        for bkt, symbol, bar, reason in pending:
            self._emit(symbol, bar, reason=reason, bucket_ms=bkt)

    def _emit(self, symbol: str, bar: dict, reason: str, bucket_ms: int) -> None:
        self._bars_emitted += 1
        self._last_bar_ts = datetime.now(timezone.utc).isoformat()
        self._last_bar_symbol = symbol
        self._last_bar_bucket = bucket_ms
        print(
            f"[feed] emit 1d {symbol} bucket={bucket_ms} close={bar['close']:.6g} via={reason}",
            flush=True,
        )
        try:
            self.on_bar(symbol, bar)
        except Exception as e:
            print(f"[feed] ERROR on_bar {symbol}: {e!r}", flush=True)
            raise
        self._write_health()

    def _write_health(self):
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": self._cycle,
            "symbols": len(self.symbols),
            "bars_emitted": self._bars_emitted,
            "fetch_ok": self._fetch_ok,
            "fetch_fail": self._fetch_fail,
            "last_bar_ts": self._last_bar_ts,
            "last_bar_symbol": self._last_bar_symbol,
            "last_bar_bucket": self._last_bar_bucket,
            "bar_minutes": BAR_MINUTES,
            "forming_buckets": {s: c["bucket"] for s, c in self._cur.items()},
        }
        try:
            tmp = self.health_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.health_path)
        except Exception as e:
            print(f"[feed] health write failed: {e!r}", flush=True)

    def _ingest_5m(self, symbol: str, ohlcv: list, now_ms: int | None = None):
        wall = int(time.time() * 1000) if now_ms is None else int(now_ms)
        for row in ohlcv:
            ts_ms, o, h, l, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
            bucket = _floor_day_ms(int(ts_ms))
            if bucket > wall:
                continue
            cur = self._cur.get(symbol)
            if cur is not None and cur["bucket"] == bucket:
                b = cur["bar"]
                b["high"] = max(b["high"], float(h))
                b["low"] = min(b["low"], float(l))
                b["close"] = float(c)
                b["volume"] += float(v)
                continue
            if cur is not None:
                if bucket < cur["bucket"]:
                    continue
                if not bucket_is_closed(cur["bucket"], wall):
                    key = (symbol, int(cur["bucket"]))
                    if key not in self._early_hold_logged:
                        self._early_hold_logged.add(key)
                        print(
                            f"[feed] hold 1d {symbol} (next-bucket 5m before close "
                            f"cur={cur['bucket']} got={bucket})",
                            flush=True,
                        )
                    continue
                bar = cur["bar"].copy()
                bar["ts"] = bar_ts_ns_from_bucket_ms(cur["bucket"])
                self._queue(symbol, bar, reason="bucket-cross")
            self._cur[symbol] = {
                "bucket": bucket,
                "bar": {
                    "ts": bar_ts_ns_from_bucket_ms(bucket),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                },
            }

    def _maybe_emit_closed(self, symbol: str, now_ms: int | None = None):
        cur = self._cur.get(symbol)
        if not cur:
            return
        wall = int(time.time() * 1000) if now_ms is None else int(now_ms)
        bucket = cur["bucket"]
        if bucket_is_closed(bucket, wall):
            bar = cur["bar"].copy()
            bar["ts"] = bar_ts_ns_from_bucket_ms(bucket)
            self._queue(symbol, bar, reason="time-close")
            del self._cur[symbol]

    def _safe_fetch(self, symbol: str, limit: int = 100, max_retries: int = 4):
        delay = 2.0
        last_err = None
        for _attempt in range(max_retries):
            try:
                product = f"{symbol}-USD"
                url = f"https://api.exchange.coinbase.com/products/{product}/candles"
                resp = requests.get(url, params={"granularity": 300}, timeout=15)
                if resp.status_code == 200:
                    raw = resp.json()
                    ohlcv = []
                    for row in raw[:limit]:
                        ts_ms = int(row[0]) * 1000
                        low = float(row[1])
                        high = float(row[2])
                        open_ = float(row[3])
                        close = float(row[4])
                        vol = float(row[5])
                        ohlcv.append([ts_ms, open_, high, low, close, vol])
                    self._fetch_ok += 1
                    return ohlcv
                last_err = f"HTTP {resp.status_code}"
                time.sleep(delay)
                delay = min(delay * 2, 20)
            except Exception as e:
                last_err = repr(e)
                time.sleep(min(5, delay))
                delay = min(delay * 2, 20)
        self._fetch_fail += 1
        if self._fetch_fail <= 5 or self._fetch_fail % 50 == 0:
            print(f"[feed] fetch fail {symbol}: {last_err}", flush=True)
        return None

    def run(self):
        self._stop = False
        self._cycle = 0
        per_symbol_sleep = float(os.getenv("FEED_SYMBOL_SLEEP", "2"))
        print(
            f"[feed] daily run start symbols={len(self.symbols)} "
            f"symbol_sleep={per_symbol_sleep}s poll_sleep={self.poll_sleep}s",
            flush=True,
        )
        self._write_health()
        while not self._stop:
            t0 = time.time()
            ok = fail = 0
            for sym in self.symbols:
                if self._stop:
                    break
                ohlcv = self._safe_fetch(sym, limit=300)
                if ohlcv:
                    ok += 1
                    ohlcv.sort(key=lambda r: r[0])
                    self._ingest_5m(sym, ohlcv)
                else:
                    fail += 1
                self._maybe_emit_closed(sym)
                time.sleep(per_symbol_sleep)
            self._flush_pending()
            self._cycle += 1
            dt = time.time() - t0
            print(
                f"[feed] cycle={self._cycle} ok={ok} fail={fail} "
                f"emitted_total={self._bars_emitted} dt={dt:.1f}s",
                flush=True,
            )
            self._write_health()
            time.sleep(self.poll_sleep)

    def stop(self):
        self._stop = True
