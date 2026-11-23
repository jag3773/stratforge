#!/usr/bin/env python3
"""One-factor then combo grid of SSOT DNA knobs for 3m vs 6m yield.

Does not touch paper/live. CASH 40 bps, live-50, SignalEngine replay.
"""
from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dateutil.relativedelta import relativedelta  # noqa: E402

import stratforge  # noqa: E402
from strategies.research_replay import (  # noqa: E402
    load_4h_bars,
    load_strategy_cfg,
    run_portfolio_replay,
    warmup_start,
)
from strategies.universe import load_live_universe  # noqa: E402

FEE = 0.004
OUT = ROOT / "results" / "backtests" / "horizon_compress"
WINDOWS_3M = [
    ("2026-01-02", "2026-04-02"),
    ("2026-02-02", "2026-05-02"),
    ("2026-03-02", "2026-06-02"),
    ("2026-04-02", "2026-07-02"),
    ("2026-05-02", "2026-08-02"),
    ("2026-06-02", "2026-09-02"),
]

# SSOT DNA knobs only. Skip rejected families (atr_stop, btc_regime, cat_stop, rank).
ONE_FACTOR: list[tuple[str, dict]] = [
    ("ssot", {}),
    # channel (milder than killed 20/30)
    ("ch54", {"channel_period": 54}),
    ("ch48", {"channel_period": 48}),
    ("ch42", {"channel_period": 42}),
    ("ch36", {"channel_period": 36}),
    # slow EMA (mild)
    ("ema220", {"slow_ema_period": 220}),
    ("ema180", {"slow_ema_period": 180}),
    ("ema160", {"slow_ema_period": 160}),
    # exit buffer: + holds longer, - exits sooner (pct of EMA)
    ("buf_m1", {"slow_ema_buffer": -1.0}),
    ("buf_m2", {"slow_ema_buffer": -2.0}),
    ("buf_p1", {"slow_ema_buffer": 1.0}),
    ("buf_p2", {"slow_ema_buffer": 2.0}),
    # cooldown
    ("cd1", {"cooldown_bars": 1}),
    ("cd3", {"cooldown_bars": 3}),
    # trend filter
    ("nofilter", {"trend_filter": False}),
    # rotate hold
    ("h8", {"rotate_min_hold_bars": 8}),
    ("h10", {"rotate_min_hold_bars": 10}),
    ("h12", {"rotate_min_hold_bars": 12}),
    ("h18", {"rotate_min_hold_bars": 18}),
    # concurrent
    ("max6", {"max_concurrent": 6}),
    ("max10", {"max_concurrent": 10}),
    ("max12", {"max_concurrent": 12}),
    # alloc clip (paper sizes equity * max_alloc)
    ("alloc15", {"max_alloc_pct": 0.15}),
    ("alloc20", {"max_alloc_pct": 0.20}),
    ("alloc30", {"max_alloc_pct": 0.30}),
    ("alloc35", {"max_alloc_pct": 0.35}),
    ("alloc40", {"max_alloc_pct": 0.40}),
    ("alloc50", {"max_alloc_pct": 0.50}),
    # 3h bar, same bar-counts (milder TF than 1h)
    ("bar180", {"bar_minutes": 180}),
]


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def load_cache(pairs, bm, start, end):
    out = {}
    for s in pairs:
        bars = load_4h_bars(s, start=start, end=end, bar_minutes=bm)
        if bars:
            out[s] = bars
    return out


def run_variant(name, overlay, base, pairs, caches, windows):
    cfg = deepcopy(base)
    cfg.update(overlay)
    tf = int(cfg.get("bar_minutes", 240) or 240)
    cache = caches[tf]
    rows = []
    for a, b in windows:
        r = run_portfolio_replay(
            pairs,
            strategy_cfg=cfg,
            start=a,
            end=b,
            start_usd=1000.0,
            fee_rate=FEE,
            bars_by_symbol=cache,
        )
        cap = int(cfg.get("max_concurrent", 8) or 8)
        rows.append(
            {
                "start": a,
                "end": b,
                "ret": round(r.net_return_pct * 100.0, 2),
                "dd": round(r.max_dd_pct * 100.0, 2),
                "trades": r.trades,
                "open_n": r.open_positions,
                "open_max": r.open_max,
                "open_ok": r.open_max <= cap,
            }
        )
    rets = [x["ret"] for x in rows]
    dds = [x["dd"] for x in rows]
    return {
        "variant": name,
        "overlay": overlay,
        "avg": round(mean(rets), 2),
        "med": round(median(rets), 2),
        "min": round(min(rets), 2),
        "max": round(max(rets), 2),
        "pos": sum(x > 0 for x in rets),
        "avg_dd": round(mean(dds), 2),
        "worst_dd": round(max(dds), 2),
        "avg_trades": round(mean(x["trades"] for x in rows), 1),
        "open_ok": all(x["open_ok"] for x in rows),
        "open_max_max": max(x["open_max"] for x in rows),
        "rows": rows,
        "cfg": {
            "channel_period": cfg.get("channel_period"),
            "slow_ema_period": cfg.get("slow_ema_period", 200),
            "slow_ema_buffer": cfg.get("slow_ema_buffer", 0.0),
            "cooldown_bars": cfg.get("cooldown_bars"),
            "trend_filter": cfg.get("trend_filter"),
            "max_concurrent": cfg.get("max_concurrent"),
            "max_alloc_pct": cfg.get("max_alloc_pct"),
            "rotate_min_hold_bars": cfg.get("rotate_min_hold_bars"),
            "bar_minutes": cfg.get("bar_minutes"),
            "exit_mode": cfg.get("exit_mode"),
            "atr_stop_mult": cfg.get("atr_stop_mult"),
        },
    }


def full_windows(months: int):
    end_cap = datetime(2026, 9, 2).date()
    out = []
    cur = datetime(2023, 7, 2).date()
    while True:
        e = cur + relativedelta(months=months)
        if e > end_cap:
            break
        out.append((cur.isoformat(), e.isoformat()))
        cur += relativedelta(months=1)
    return out


def main() -> None:
    pairs = load_live_universe()
    base = load_strategy_cfg(ROOT / "config" / "trend_rotation_25_max8_rotate.json")
    OUT.mkdir(parents=True, exist_ok=True)
    dummy = deepcopy(base)
    data_start = warmup_start(_dt("2026-01-02"), dummy).date().isoformat()
    tfs = sorted({int((deepcopy(base) | ov).get("bar_minutes", 240) or 240) for _, ov in ONE_FACTOR})
    caches = {}
    print(f"pairs={len(pairs)} {data_start}→2026-09-02 tfs={tfs}", flush=True)
    for tf in tfs:
        t0 = time.time()
        caches[tf] = load_cache(pairs, tf, data_start, "2026-09-02")
        print(f"  tf={tf} coins={len(caches[tf])} {time.time()-t0:.1f}s", flush=True)

    results = []
    for name, ov in ONE_FACTOR:
        t0 = time.time()
        rec = run_variant(name, ov, base, pairs, caches, WINDOWS_3M)
        rec["secs"] = round(time.time() - t0, 2)
        results.append(rec)
        print(
            f"{name:10} avg={rec['avg']:+6.1f} med={rec['med']:+6.1f} min={rec['min']:+6.1f} "
            f"pos={rec['pos']}/6 dd={rec['worst_dd']:.0f} om={rec['open_max_max']} ok={rec['open_ok']} "
            f"{ov}",
            flush=True,
        )

    ssot = next(r for r in results if r["variant"] == "ssot")
    # 2026 SSOT 6m mean from prior screen ~34.1; recompute quickly
    ssot6 = run_variant("ssot6", {}, base, pairs, caches, [
        ("2026-01-02", "2026-07-02"),
        ("2026-02-02", "2026-08-02"),
        ("2026-03-02", "2026-09-02"),
    ])
    target_mean = ssot6["avg"]
    target_med = ssot6["med"]
    print(f"\nSSOT 2026 6m avg={target_mean:+.1f} med={target_med:+.1f}", flush=True)
    print(f"SSOT 2026 3m avg={ssot['avg']:+.1f} med={ssot['med']:+.1f} min={ssot['min']:+.1f}", flush=True)

    def pass_screen(r):
        return (
            r["open_ok"]
            and r["avg"] >= target_mean * 0.75
            and r["med"] >= ssot["med"] - 5.0
            and r["min"] >= ssot["min"] - 8.0
        )

    for r in results:
        r["screen_pass"] = pass_screen(r)
        r["gap_vs_6m_avg"] = round(r["avg"] - target_mean, 2)

    # Combos from knobs that raised 3m avg without blowing min
    lifted = [
        r
        for r in results
        if r["variant"] != "ssot"
        and r["open_ok"]
        and r["avg"] > ssot["avg"] + 2.0
        and r["min"] >= ssot["min"] - 8.0
    ]
    lifted.sort(key=lambda x: x["avg"], reverse=True)
    print("\nLifted vs SSOT 3m avg:", [(r["variant"], r["avg"], r["min"]) for r in lifted], flush=True)

    combos: list[tuple[str, dict]] = []
    # Build pairwise combos of top overlays (max 12)
    top = lifted[:5]
    for i, a in enumerate(top):
        for b in top[i + 1 :]:
            ov = {**a["overlay"], **b["overlay"]}
            if ov == a["overlay"] or ov == b["overlay"]:
                continue
            combos.append((f"{a['variant']}+{b['variant']}", ov))
    # Hand combos that match the goal (size + slightly faster signal)
    hand = [
        ("alloc35_ch48", {"max_alloc_pct": 0.35, "channel_period": 48}),
        ("alloc35_ch42", {"max_alloc_pct": 0.35, "channel_period": 42}),
        ("alloc40_ch48", {"max_alloc_pct": 0.40, "channel_period": 48}),
        ("alloc40_h10", {"max_alloc_pct": 0.40, "rotate_min_hold_bars": 10}),
        ("alloc35_ema180", {"max_alloc_pct": 0.35, "slow_ema_period": 180}),
        ("alloc30_ch48_h10", {"max_alloc_pct": 0.30, "channel_period": 48, "rotate_min_hold_bars": 10}),
        ("alloc35_bufm1", {"max_alloc_pct": 0.35, "slow_ema_buffer": -1.0}),
        ("max10_alloc20", {"max_concurrent": 10, "max_alloc_pct": 0.20}),
        ("max6_alloc35", {"max_concurrent": 6, "max_alloc_pct": 0.35}),
        ("ch48_ema180_alloc35", {"channel_period": 48, "slow_ema_period": 180, "max_alloc_pct": 0.35}),
        ("ch42_ema180_alloc40_h10", {"channel_period": 42, "slow_ema_period": 180, "max_alloc_pct": 0.40, "rotate_min_hold_bars": 10}),
    ]
    seen = {json.dumps(ov, sort_keys=True) for _, ov in combos}
    for n, ov in hand:
        k = json.dumps(ov, sort_keys=True)
        if k not in seen:
            combos.append((n, ov))
            seen.add(k)

    combo_results = []
    for name, ov in combos:
        t0 = time.time()
        rec = run_variant(name, ov, base, pairs, caches, WINDOWS_3M)
        rec["secs"] = round(time.time() - t0, 2)
        rec["screen_pass"] = pass_screen(rec)
        rec["gap_vs_6m_avg"] = round(rec["avg"] - target_mean, 2)
        combo_results.append(rec)
        flag = "PASS" if rec["screen_pass"] else "fail"
        print(
            f"{flag} {name:28} avg={rec['avg']:+6.1f} med={rec['med']:+6.1f} min={rec['min']:+6.1f} "
            f"gap6={rec['gap_vs_6m_avg']:+5.1f} {ov}",
            flush=True,
        )

    payload = {
        "ssot_3m": {k: ssot[k] for k in ("avg", "med", "min", "max", "pos")},
        "ssot_6m": {k: ssot6[k] for k in ("avg", "med", "min", "max", "pos")},
        "target_6m_avg": target_mean,
        "one_factor": results,
        "combos": combo_results,
    }
    (OUT / "ssot_knob_grid_2026.json").write_text(json.dumps(payload, indent=2))

    survivors = [r for r in results + combo_results if r["screen_pass"] and r["variant"] != "ssot"]
    survivors.sort(key=lambda x: (x["avg"], x["min"]), reverse=True)
    print(f"\n2026 survivors {len(survivors)}", flush=True)
    for r in survivors[:12]:
        print(f"  {r['variant']:28} avg={r['avg']:+.1f} med={r['med']:+.1f} min={r['min']:+.1f} {r['overlay']}", flush=True)

    if not survivors:
        # Promote closest 3 by 3m avg among open_ok + min not wrecked
        pool = [
            r
            for r in results + combo_results
            if r["variant"] != "ssot" and r["open_ok"] and r["min"] >= ssot["min"] - 8.0
        ]
        pool.sort(key=lambda x: x["avg"], reverse=True)
        survivors = pool[:4]
        print("No 75%-of-6m-mean pass; promoting closest by 3m avg:", [s["variant"] for s in survivors], flush=True)

    # Full 3m + 6m for ssot + top survivors (cap 5)
    promote = [("ssot", {})] + [(s["variant"], s["overlay"]) for s in survivors[:5]]
    # Dedup overlays
    uniq = []
    seen_ov = set()
    for n, ov in promote:
        k = json.dumps(ov, sort_keys=True)
        if k in seen_ov:
            continue
        seen_ov.add(k)
        uniq.append((n, ov))
    promote = uniq

    data_start_full = warmup_start(_dt("2023-07-02"), dummy).date().isoformat()
    t0 = time.time()
    cache_full = load_cache(pairs, 240, data_start_full, "2026-09-02")
    print(f"\nfull cache {len(cache_full)} {time.time()-t0:.1f}s", flush=True)
    caches_full = {240: cache_full}
    # any 3h survivor
    if any(int((deepcopy(base) | ov).get("bar_minutes", 240) or 240) == 180 for _, ov in promote):
        caches_full[180] = load_cache(pairs, 180, data_start_full, "2026-09-02")

    full = {"3m": [], "6m": []}
    for months, key in ((3, "3m"), (6, "6m")):
        wins = full_windows(months)
        print(f"\n=== FULL {key} n={len(wins)} ===", flush=True)
        for name, ov in promote:
            rec = run_variant(name, ov, base, pairs, caches_full, wins)
            full[key].append(rec)
            print(
                f"  {name:28} avg={rec['avg']:+6.1f} med={rec['med']:+6.1f} min={rec['min']:+6.1f} "
                f"pos={rec['pos']}/{rec['rows'] and len(rec['rows'])} om={rec['open_max_max']}",
                flush=True,
            )

    (OUT / "ssot_knob_grid_full.json").write_text(json.dumps({"promote": [{"n": n, "ov": ov} for n, ov in promote], "full": full}, indent=2))
    print(f"\nWrote {OUT/'ssot_knob_grid_2026.json'} and ssot_knob_grid_full.json", flush=True)


if __name__ == "__main__":
    main()
