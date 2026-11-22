#!/usr/bin/env python3
"""Isolated NEW strategy families (research + isolated paper soak).

Never imported by live_node. paper_family_node.py may import FamilyEngine
for the donch_d20_10 paper soak only (separate journal/clock/lock).

CASH PaperExecutor, 40 bps in research staggers, live-50. Families:
  donch_d20_10 / donch_d55_20  — daily Donchian entry/exit (turtle, long-only, no ATR stop)
  donch_4h_60_20               — 4h Donchian 60 entry / 20 exit (EMA exit removed)
  dualma_d21_55 / dualma_d50_200 — daily EMA cross
  cs_mom_63 / cs_mom_126       — monthly top-8 cross-sectional momentum (equal 12.5%)
  tsmom_252                    — monthly TSMOM: positive 252d, top-8 by strength
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from strategies.paper_executor import PaperExecutor  # noqa: E402


def _ema_update(prev: float | None, price: float, n: int) -> float:
    if prev is None:
        return price
    k = 2.0 / (n + 1.0)
    return prev * (1.0 - k) + price * k


class FamilyEngine:
    def __init__(self, cfg: dict, symbols: list[str]):
        self.cfg = cfg
        self.kind = str(cfg["family"])
        self.max_conc = int(cfg.get("max_concurrent", 8))
        self.hold_bars = int(cfg.get("rotate_min_hold_bars", 3))
        self.rotate = bool(cfg.get("rotate_when_full", True))
        self.entry_n = int(cfg.get("entry_n", 20))
        self.exit_n = int(cfg.get("exit_n", 10))
        self.fast_n = int(cfg.get("fast_n", 21))
        self.slow_n = int(cfg.get("slow_n", 55))
        self.lookback = int(cfg.get("lookback_bars", 126))
        self.hh: dict[str, deque] = {s: deque(maxlen=max(self.entry_n, self.exit_n) + 2) for s in symbols}
        self.ll: dict[str, deque] = {s: deque(maxlen=max(self.entry_n, self.exit_n) + 2) for s in symbols}
        self.cc: dict[str, deque] = {s: deque(maxlen=max(self.lookback, 260) + 5) for s in symbols}
        self.fast: dict[str, float | None] = {s: None for s in symbols}
        self.slow: dict[str, float | None] = {s: None for s in symbols}
        self.slots: set[str] = set()
        self.entry_ts: dict[str, int] = {}
        self.entry_px: dict[str, float] = {}
        self.bars_held: dict[str, int] = defaultdict(int)
        self.last_px: dict[str, float] = {}
        self._last_rebal_key = ""

    def sync_from_positions(self, positions: dict) -> None:
        self.slots = {s for s, p in positions.items() if p}
        for s in list(self.entry_ts):
            if s not in self.slots:
                self.entry_ts.pop(s, None)
                self.entry_px.pop(s, None)

    def _hold_ok(self, sym: str, ts: int) -> bool:
        t0 = int(self.entry_ts.get(sym) or 0)
        if t0 <= 0:
            return self.bars_held.get(sym, 0) >= self.hold_bars
        # bar duration encoded in cfg minutes
        mins = int(self.cfg.get("bar_minutes", 1440) or 1440)
        return (ts - t0) >= self.hold_bars * mins * 60 * 1_000_000_000

    def _victim(self, exclude: str, ts: int) -> str | None:
        elig = []
        for s in self.slots:
            if s == exclude:
                continue
            ep = self.entry_px.get(s) or 0.0
            px = self.last_px.get(s) or ep
            if ep <= 0:
                continue
            ret = px / ep - 1.0
            if ret >= 0:
                continue
            if not self._hold_ok(s, ts):
                continue
            elig.append((ret, s))
        if not elig:
            return None
        elig.sort()
        return elig[0][1]

    def update(self, sym: str, bar: dict) -> None:
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        self.hh[sym].append(h)
        self.ll[sym].append(l)
        self.cc[sym].append(c)
        self.last_px[sym] = c
        self.fast[sym] = _ema_update(self.fast[sym], c, self.fast_n)
        self.slow[sym] = _ema_update(self.slow[sym], c, self.slow_n)
        if sym in self.slots:
            self.bars_held[sym] += 1

    def _maybe_enter(self, sym: str, ts: int, px: float) -> list[dict]:
        acts = []
        if sym in self.slots:
            return acts
        if len(self.slots) >= self.max_conc:
            if not self.rotate:
                return acts
            v = self._victim(sym, ts)
            if v is None:
                return acts
            acts.append({"type": "CLOSE", "symbol": v, "price": float(self.last_px.get(v) or px), "reason": "rotate_out"})
            self.slots.discard(v)
            self.entry_ts.pop(v, None)
            self.entry_px.pop(v, None)
            self.bars_held[v] = 0
        if len(self.slots) >= self.max_conc:
            return acts
        acts.append({"type": "BUY", "symbol": sym, "price": px, "reason": f"{self.kind}_long"})
        self.slots.add(sym)
        self.entry_ts[sym] = ts
        self.entry_px[sym] = px
        self.bars_held[sym] = 0
        return acts

    def on_bar_stream(self, sym: str, bar: dict, ts: int) -> list[dict]:
        """Per-symbol stream families (donch / dualma)."""
        self.update(sym, bar)
        px = float(bar["close"])
        acts: list[dict] = []
        kind = self.kind
        if kind.startswith("donch"):
            if len(self.hh[sym]) < self.entry_n + 1:
                return acts
            hh = max(list(self.hh[sym])[-self.entry_n - 1 : -1])
            xl = min(list(self.ll[sym])[-self.exit_n - 1 : -1])
            if sym in self.slots and px < xl:
                acts.append({"type": "CLOSE", "symbol": sym, "price": px, "reason": "donch_exit"})
                self.slots.discard(sym)
                self.entry_ts.pop(sym, None)
                self.entry_px.pop(sym, None)
                self.bars_held[sym] = 0
                return acts
            if sym not in self.slots and px > hh:
                acts.extend(self._maybe_enter(sym, ts, px))
        elif kind.startswith("dualma"):
            f, s = self.fast[sym], self.slow[sym]
            if f is None or s is None:
                return acts
            if len(self.cc[sym]) < self.slow_n:
                return acts
            if sym in self.slots and f < s:
                acts.append({"type": "CLOSE", "symbol": sym, "price": px, "reason": "ma_cross_down"})
                self.slots.discard(sym)
                self.entry_ts.pop(sym, None)
                self.entry_px.pop(sym, None)
                self.bars_held[sym] = 0
                return acts
            if sym not in self.slots and f > s:
                acts.extend(self._maybe_enter(sym, ts, px))
        return acts

    def rebalance_key(self, ts: int) -> str:
        dt = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
        if str(self.cfg.get("rebalance", "month")) == "week":
            return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        return f"{dt.year:04d}-{dt.month:02d}"

    def on_month_group(self, ts: int, marks: dict[str, float]) -> list[dict]:
        """Calendar rebalance families. marks = last close per symbol this bar-ts."""
        key = self.rebalance_key(ts)
        if key == self._last_rebal_key:
            return []
        # Need lookback
        scores = []
        for s, px in marks.items():
            closes = list(self.cc[s])
            if len(closes) < self.lookback + 1:
                continue
            past = closes[-self.lookback - 1]
            if past <= 0:
                continue
            ret = px / past - 1.0
            scores.append((ret, s, px))
        if not scores:
            return []
        self._last_rebal_key = key
        if self.kind.startswith("tsmom"):
            pos = [x for x in scores if x[0] > 0]
            pos.sort(reverse=True)
            target = {s for _, s, _ in pos[: self.max_conc]}
        else:
            scores.sort(reverse=True)
            # top-K, skip non-positive if enough
            pos = [x for x in scores if x[0] > 0]
            pick = pos[: self.max_conc] if len(pos) >= 1 else scores[: self.max_conc]
            target = {s for _, s, _ in pick[: self.max_conc]}
        acts: list[dict] = []
        for s in list(self.slots):
            if s not in target:
                acts.append({"type": "CLOSE", "symbol": s, "price": float(marks.get(s) or self.last_px.get(s) or 0), "reason": "rebalance_out"})
                self.slots.discard(s)
                self.entry_ts.pop(s, None)
                self.entry_px.pop(s, None)
        for ret, s, px in sorted(scores, reverse=True):
            if s not in target or s in self.slots:
                continue
            if len(self.slots) >= self.max_conc:
                break
            acts.append({"type": "BUY", "symbol": s, "price": px, "reason": f"{self.kind}_rebalance"})
            self.slots.add(s)
            self.entry_ts[s] = ts
            self.entry_px[s] = px
        return acts


def run_family_replay(
    symbols: list[str],
    *,
    cfg: dict,
    bars_by_symbol: dict[str, list[dict]],
    start: str,
    end: str,
    start_usd: float = 1000.0,
    fee_rate: float = 0.004,
) -> dict:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    start_ns = int(start_dt.timestamp() * 1_000_000_000)
    end_ns = int(end_dt.timestamp() * 1_000_000_000)
    per = {}
    for s in symbols:
        src = bars_by_symbol.get(s) or []
        bars = [b for b in src if start_ns <= int(b["ts"]) <= end_ns]  # warmup loaded in cache before start
        # include warmup bars before start
        warm = [b for b in src if int(b["ts"]) < start_ns]
        if warm or bars:
            per[s.upper()] = warm + bars
    stream: list[tuple[int, str, dict]] = []
    for s, bars in per.items():
        for b in bars:
            stream.append((int(b["ts"]), s, b))
    stream.sort(key=lambda x: (x[0], x[1]))
    eng = FamilyEngine(cfg, list(per.keys()))
    fd, journal = tempfile.mkstemp(prefix="fam_replay_", suffix=".jsonl")
    os.close(fd)
    try:
        ex = PaperExecutor(
            start_usd=start_usd,
            max_alloc_pct=float(cfg.get("max_alloc_pct", 0.25)),
            journal_path=journal,
            fee_rate=fee_rate,
            write_opening=False,
        )
        curve = []
        open_max = 0
        n_bars = 0
        kind = cfg["family"]
        cal = kind.startswith("cs_mom") or kind.startswith("tsmom")
        from itertools import groupby

        def consume(acts, ts):
            nonlocal open_max
            if not acts:
                return
            prepared = [{**a, "price": a.get("price")} for a in acts]
            ex.apply(prepared)
            # rebuild slots from fills
            eng.sync_from_positions(ex.positions)
            for s, p in ex.positions.items():
                eng.entry_px[s] = float(p.get("entry") or 0)
                if s not in eng.entry_ts:
                    eng.entry_ts[s] = ts
            open_max = max(open_max, len(ex.positions))

        if cal:
            for ts, grp in groupby(stream, key=lambda r: r[0]):
                items = [(s, b) for _, s, b in grp]
                for s, b in items:
                    eng.update(s, b)
                    ex.update_price(s, float(b["close"]))
                    n_bars += 1
                if ts < start_ns:
                    continue
                marks = {s: float(b["close"]) for s, b in items}
                consume(eng.on_month_group(ts, marks), ts)
                curve.append((ts, ex.equity()))
        else:
            for ts, sym, bar in stream:
                emit = ts >= start_ns
                px = float(bar["close"])
                ex.update_price(sym, px)
                if not emit:
                    eng.update(sym, bar)
                    n_bars += 1
                    continue
                acts = eng.on_bar_stream(sym, bar, ts)
                n_bars += 1
                if acts:
                    consume(acts, ts)
                open_max = max(open_max, len(ex.positions))
                curve.append((ts, ex.equity()))

        eq = ex.equity()
        peak = 0.0
        max_dd = 0.0
        for _, e in curve:
            peak = max(peak, e)
            if peak > 0:
                max_dd = max(max_dd, (peak - e) / peak)
        open_n = len([1 for p in ex.positions.values() if float(p.get("qty") or 0) * float(ex.last_price.get(list(ex.positions.keys())[0] if False else "", 0) or 0) or True])
        open_n = len(ex.positions)
        return {
            "end_equity": eq,
            "end_cash": float(ex.cash),
            "ret_pct": (eq / start_usd - 1.0) * 100.0 if start_usd else 0.0,
            "max_dd_pct": max_dd * 100.0,
            "trades": int(ex.trades),
            "wins": int(ex.wins),
            "losses": int(ex.losses),
            "open_n": open_n,
            "open_max": open_max,
            "bars": n_bars,
        }
    finally:
        try:
            os.unlink(journal)
        except OSError:
            pass
