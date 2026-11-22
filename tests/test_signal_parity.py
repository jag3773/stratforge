#!/usr/bin/env python3
"""
Signal parity / smoke: replay recent parquet 4h bars through SignalEngine
with trend-rotation 25 config. Confirms engine emits structured actions
and is deterministic across two passes.
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from strategies.signal_engine import SignalEngine, SignalConfig
from strategies.live_data_feed import _load_seed_4h

CFG_PATH = os.path.join(ROOT, "config", "trend_rotation_25_max8_rotate.json")


def load_cfg() -> SignalConfig:
    with open(CFG_PATH) as f:
        cfg = json.load(f)
    return SignalConfig(
        mode=cfg.get("mode", "breakout"),
        channel_period=cfg.get("channel_period", 60),
        atr_stop_mult=float(cfg.get("atr_stop_mult", 0.0)),
        cooldown_bars=int(cfg.get("cooldown_bars", 2)),
        trend_filter=bool(cfg.get("trend_filter", True)),
        long_only=bool(cfg.get("long_only", True)),
        exit_mode=cfg.get("exit_mode", "slow_ema"),
        slow_ema_buffer=float(cfg.get("slow_ema_buffer", 0.0)),
        max_concurrent=int(cfg.get("max_concurrent", 8)),
        max_alloc_pct=float(cfg.get("max_alloc_pct", 0.25)),
        use_adx_filter=bool(cfg.get("use_adx_filter", False)),
        rotate_when_full=bool(cfg.get("rotate_when_full", True)),
        rotate_min_hold_bars=int(cfg.get("rotate_min_hold_bars", 14) or 14),
        bar_minutes=int(cfg.get("bar_minutes", 240) or 240),
    )


def replay(symbols: list[str], emit_after_warm: int = 80):
    cfg = load_cfg()
    eng = SignalEngine(cfg, symbols)
    positions: dict = {}
    actions_log = []
    for sym in symbols:
        bars = _load_seed_4h(sym)
        if len(bars) < cfg.channel_period + 10:
            continue
        warm_n = max(0, len(bars) - emit_after_warm)
        for b in bars[:warm_n]:
            eng.on_bar(sym, b, emit=False)
        for b in bars[warm_n:]:
            acts = eng.on_bar(sym, b, emit=True, positions=positions)
            for a in acts:
                a = {**a, "price": a.get("price", b["close"])}
                actions_log.append((sym, b["ts"], a["type"], a.get("reason"), a.get("price"), a.get("symbol")))
                if a["type"] == "BUY":
                    positions[a["symbol"]] = {
                        "side": "LONG",
                        "entry": float(a["price"]),
                        "ts": int(b.get("ts") or 0),
                    }
                    eng.sync_slots_from_positions(positions)
                elif a["type"] == "CLOSE":
                    positions.pop(a["symbol"], None)
                    eng.sync_slots_from_positions(positions)
    return actions_log


def main():
    symbols = ["BTC", "ETH", "SOL", "XRP", "LINK"]
    a1 = replay(symbols)
    a2 = replay(symbols)
    assert a1 == a2, "non-deterministic SignalEngine replay"
    buys = sum(1 for x in a1 if x[2] == "BUY")
    closes = sum(1 for x in a1 if x[2] == "CLOSE")
    print(f"strategy=trend_rotation_25_max8_rotate symbols={symbols}")
    print(f"actions_total={len(a1)} buys={buys} closes={closes}")
    print("deterministic=yes")
    for row in a1[:8]:
        print(" sample", row)
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
