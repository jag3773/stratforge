#!/usr/bin/env python3
"""
Resumable 1m OHLCV downloader for Coinbase spot USD pairs.

Writes data/raw/<BASE>_USD_1m.parquet only (never 5m, never Nautilus catalog).

Handles Coinbase pitfalls:
  1. Empty return when since predates first trade -> 30d bracket + 1-candle refine.
  2. 400 "start in the future" -> clamp since/mid to LIVE()-STEP_MS.
  3. Infinite loop on resume-near-now -> break when since reaches present.
  4. Every fetch_ohlcv goes through safe_fetch (429 must not kill the run).
  5. Backfill [GLOBAL_START, min(have)) AND forward from max(have)+STEP (do not
     skip the early gap by resuming only from the latest row).
  6. Skip when both ends are done: min<=GLOBAL_START and max within 2d of now.

Default universe: union of 5m warehouse bases + config/universe_live.json.
Override: COINS=BTC,ETH  START_DATE=YYYY-MM-DD  SLEEP=0.6  MAX_CHUNKS=N (smoke)

Usage:
  python -u scripts/download_coinbase_1m.py
  COINS=HYPE python -u scripts/download_coinbase_1m.py
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

import ccxt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from strategies.display_time import format_et  # noqa: E402

OUT_DIR = ROOT / "data" / "raw"
LOCK_PATH = OUT_DIR / "_download_1m.lock"
PROGRESS_PATH = OUT_DIR / "_download_1m_progress.json"
TF = "1m"
STEP_MS = 60 * 1000
LIMIT = 300
DAY_MS = 24 * 60 * 60 * 1000
TAIL_MS = 7 * DAY_MS
BRACKET_MS = 30 * DAY_MS
CHECKPOINT_ROWS = 20000
RETRY_SLEEP = 12
SLEEP = float(os.environ.get("SLEEP", "0.6"))
_START_OVERRIDE = os.environ.get("START_DATE", "2020-01-01")
GLOBAL_START = int(pd.Timestamp(_START_OVERRIDE, tz="UTC").timestamp() * 1000)
MAX_CHUNKS = int(os.environ["MAX_CHUNKS"]) if os.environ.get("MAX_CHUNKS") else None
COLS = ["timestamp", "open", "high", "low", "close", "volume"]


def LIVE() -> int:
    return int(time.time() * 1000)


def ts_et(ms: int) -> str:
    return format_et(ms, timespec="minutes") or str(ms)


def safe_fetch(ex, pair: str, since: int, limit: int = LIMIT):
    delay = RETRY_SLEEP
    since = min(int(since), LIVE() - STEP_MS)
    if since >= LIVE() - STEP_MS:
        return []
    for _ in range(4):
        try:
            return ex.fetch_ohlcv(pair, TF, since=since, limit=limit)
        except ccxt.RateLimitExceeded:
            time.sleep(delay)
            delay = min(delay * 2, 30)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout):
            time.sleep(10)
        except ccxt.BadSymbol:
            return None
        except Exception as e:
            print(f"    fetch {type(e).__name__}: {e} -> sleep 8s", flush=True)
            time.sleep(8)
    return None


def coin_list() -> list[str]:
    if os.environ.get("COINS"):
        return [s.strip().upper() for s in os.environ["COINS"].split(",") if s.strip()]
    bases: set[str] = set()
    for p in OUT_DIR.glob("*_USD_5m.parquet"):
        bases.add(p.name.split("_USD_5m")[0].upper())
    uni = ROOT / "config" / "universe_live.json"
    if uni.exists():
        data = json.loads(uni.read_text())
        for s in data.get("symbols") or []:
            bases.add(str(s).strip().upper())
    return sorted(bases)


def load_stats(path: Path) -> tuple[int, int, int] | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["timestamp"])
    if df.empty:
        return None
    return int(df["timestamp"].min()), int(df["timestamp"].max()), int(len(df))


def find_holes(path: Path, max_gap_ms: int = 6 * 60 * 60 * 1000) -> list[tuple[int, int]]:
    """Interior gaps (e.g. crash mid-backfill concatenated onto a later block)."""
    if not path.exists():
        return []
    s = pd.read_parquet(path, columns=["timestamp"])["timestamp"].sort_values().reset_index(drop=True)
    if s.empty:
        return []
    d = s.diff()
    holes: list[tuple[int, int]] = []
    for i in d.index[d > max_gap_ms]:
        holes.append((int(s.iloc[i - 1]) + STEP_MS, int(s.iloc[i])))
    return holes


def _checkpoint(path: Path, rows: list) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows, columns=COLS)
    if path.exists():
        old = pd.read_parquet(path)
        df = pd.concat([old, df], ignore_index=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def write_progress(payload: dict) -> None:
    payload["updated_et"] = format_et(LIVE(), timespec="minutes")
    tmp = PROGRESS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, PROGRESS_PATH)


def download_range(ex, sym: str, pair: str, path: Path, since: int, until: int, label: str, progress: dict) -> int:
    """Fetch [since, until). Returns new rows written this call."""
    if since >= until:
        return 0
    rows: list = []
    written = 0
    chunks = 0
    brk = None
    print(
        f"[{sym}] {label} {ts_et(since)} -> {ts_et(until)}",
        flush=True,
    )
    while since < until and since < LIVE() - STEP_MS:
        data = safe_fetch(ex, pair, since, LIMIT)
        if data is None:
            print(f"[{sym}] {label} fetch exhausted — skip rest of range", flush=True)
            break
        if not data:
            if brk is None:
                brk = since
            nxt = min(brk + BRACKET_MS, until, LIVE() - STEP_MS)
            if nxt <= brk:
                break
            probe = safe_fetch(ex, pair, nxt, 2)
            if probe:
                a, b = brk, nxt
                while a < b:
                    mid = (a + b) // 2
                    if mid >= LIVE() - STEP_MS:
                        d = None
                    else:
                        d = safe_fetch(ex, pair, mid, 2)
                    if d:
                        b = mid
                    else:
                        a = mid + STEP_MS
                    time.sleep(SLEEP)
                brk = None
                confirm = safe_fetch(ex, pair, a, 2) if a < LIVE() - STEP_MS else None
                since = a if confirm else nxt
            else:
                brk = nxt
                since = nxt
            time.sleep(SLEEP)
            continue

        new = [r for r in data if r[0] < until]
        if not new:
            break
        rows.extend(new)
        since = data[-1][0] + STEP_MS
        chunks += 1
        if since >= LIVE() - STEP_MS or since >= until:
            pass
        if len(rows) >= CHECKPOINT_ROWS:
            _checkpoint(path, rows)
            written += len(rows)
            rows = []
            st = load_stats(path)
            n = st[2] if st else 0
            print(f"[{sym}] {label} checkpoint {ts_et(since)} ({n} total rows)", flush=True)
            progress["current"] = {
                "sym": sym,
                "phase": label,
                "since_et": ts_et(since),
                "rows": n,
            }
            write_progress(progress)
        time.sleep(SLEEP)
        if MAX_CHUNKS is not None and chunks >= MAX_CHUNKS:
            print(f"[{sym}] MAX_CHUNKS={MAX_CHUNKS} — stopping range", flush=True)
            break
        if since >= until or since >= LIVE() - STEP_MS:
            break

    if rows:
        _checkpoint(path, rows)
        written += len(rows)
    return written


def process_coin(ex, sym: str, progress: dict) -> str:
    pair = f"{sym}/USD"
    path = OUT_DIR / f"{sym}_USD_1m.parquet"
    live = LIVE()
    tail = safe_fetch(ex, pair, live - TAIL_MS, 10)
    if not tail:
        print(f"[{sym}] NO RECENT DATA (last 7d) — skipping", flush=True)
        return "dead"

    st = load_stats(path)
    live = LIVE()
    if st and st[1] >= live - 2 * DAY_MS and st[0] <= GLOBAL_START + STEP_MS:
        holes = find_holes(path)
        if not holes:
            print(f"[{sym}] complete ({st[2]} rows, {ts_et(st[0])} -> {ts_et(st[1])}) — skipping", flush=True)
            return "skip"

    # Backfill early gap (does not re-pull the already-stored middle).
    st = load_stats(path)
    if st is None or st[0] > GLOBAL_START + STEP_MS:
        until = st[0] if st else live
        download_range(ex, sym, pair, path, GLOBAL_START, until, "backfill", progress)
        if MAX_CHUNKS is not None:
            return "partial"

    st = load_stats(path)
    live = LIVE()
    if st is None:
        print(f"[{sym}] NO DATA after backfill", flush=True)
        return "empty"
    if st[1] < live - 2 * DAY_MS:
        download_range(ex, sym, pair, path, st[1] + STEP_MS, live, "forward", progress)

    if MAX_CHUNKS is None:
        for a, b in find_holes(path):
            print(f"[{sym}] hole {ts_et(a)} -> {ts_et(b)}", flush=True)
            download_range(ex, sym, pair, path, a, b, "hole", progress)

    st = load_stats(path)
    if st is None:
        return "empty"
    print(f"[{sym}] DONE {path.name} ({st[2]} rows, {ts_et(st[0])} -> {ts_et(st[1])})", flush=True)
    return "ok" if MAX_CHUNKS is None else "partial"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lock_f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"another 1m download holds {LOCK_PATH} — exit", flush=True)
        return 2
    lock_f.write(str(os.getpid()) + "\n")
    lock_f.flush()

    pairs = coin_list()
    ex = ccxt.coinbase({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    progress = {
        "pid": os.getpid(),
        "tf": TF,
        "global_start": _START_OVERRIDE,
        "sleep_s": SLEEP,
        "symbols_total": len(pairs),
        "completed": [],
        "skipped": [],
        "dead": [],
        "empty": [],
        "current": None,
    }
    write_progress(progress)
    print(
        f"=== 1m download: {len(pairs)} coins, start={_START_OVERRIDE} UTC, "
        f"sleep={SLEEP}s, out={OUT_DIR} ===",
        flush=True,
    )
    print(f"    started {format_et(LIVE(), timespec='minutes')}", flush=True)

    for i, sym in enumerate(pairs, 1):
        print(f"-- {i}/{len(pairs)} {sym} --", flush=True)
        progress["current"] = {"sym": sym, "i": i}
        write_progress(progress)
        try:
            status = process_coin(ex, sym, progress)
        except Exception as e:
            print(f"[{sym}] FATAL {type(e).__name__}: {e}", flush=True)
            progress.setdefault("errors", []).append({"sym": sym, "err": f"{type(e).__name__}: {e}"})
            write_progress(progress)
            time.sleep(8)
            continue
        bucket = {
            "ok": "completed",
            "skip": "skipped",
            "dead": "dead",
            "empty": "empty",
            "partial": "completed",
        }.get(status, "completed")
        progress[bucket].append(sym)
        progress["current"] = None
        write_progress(progress)
        if MAX_CHUNKS is not None:
            break

    print("=== 1m DOWNLOAD COMPLETE ===", flush=True)
    print(
        f"    done={len(progress['completed'])} skip={len(progress['skipped'])} "
        f"dead={len(progress['dead'])} empty={len(progress['empty'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
