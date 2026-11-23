#!/usr/bin/env python3
"""Screen trend_rotation_25_max8_rotate variants for similar % in less calendar time.

Research only. Does not touch paper/live configs.
CASH SignalEngine + PaperExecutor, 40 bps, live-50 universe unless --pairs.
"""
from __future__ import annotations

import argparse
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
START_USD = 1000.0

# Fixed-length 2026 windows. 3m is the candidate horizon; 6m is SSOT yield reference.
WINDOWS_3M = [
    ("2026-01-02", "2026-04-02"),
    ("2026-02-02", "2026-05-02"),
    ("2026-03-02", "2026-06-02"),
    ("2026-04-02", "2026-07-02"),
    ("2026-05-02", "2026-08-02"),
    ("2026-06-02", "2026-09-02"),
]
WINDOWS_6M = [
    ("2026-01-02", "2026-07-02"),
    ("2026-02-02", "2026-08-02"),
    ("2026-03-02", "2026-09-02"),
]
PAIRED = [
    # same birth: variant 3m vs SSOT 6m
    ("2026-01-02", "2026-04-02", "2026-07-02"),
    ("2026-02-02", "2026-05-02", "2026-08-02"),
    ("2026-03-02", "2026-06-02", "2026-09-02"),
]

VARIANTS: list[tuple[str, dict]] = [
    ("ssot_4h", {}),
    ("4h_ch40", {"channel_period": 40}),
    ("4h_ch30", {"channel_period": 30}),
    ("4h_ch20", {"channel_period": 20}),
    ("4h_ema150", {"slow_ema_period": 150}),
    ("4h_ema100", {"slow_ema_period": 100}),
    ("4h_ema50", {"slow_ema_period": 50}),
    ("4h_ch30_ema100", {"channel_period": 30, "slow_ema_period": 100}),
    ("4h_ch30_ema100_h7", {"channel_period": 30, "slow_ema_period": 100, "rotate_min_hold_bars": 7}),
    ("4h_ema100_h7", {"slow_ema_period": 100, "rotate_min_hold_bars": 7}),
    ("4h_ch45_ema150_h10", {"channel_period": 45, "slow_ema_period": 150, "rotate_min_hold_bars": 10}),
    ("4h_hold7", {"rotate_min_hold_bars": 7}),
    ("2h_samebars", {"bar_minutes": 120}),
    ("2h_samebars_h28", {"bar_minutes": 120, "rotate_min_hold_bars": 28}),
    ("1h_samebars", {"bar_minutes": 60}),
    ("1h_samebars_h56", {"bar_minutes": 60, "rotate_min_hold_bars": 56}),
    ("1h_ch30_ema100", {"bar_minutes": 60, "channel_period": 30, "slow_ema_period": 100}),
    ("1h_ch60_ema100_h14", {"bar_minutes": 60, "slow_ema_period": 100}),
]


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _days_to_target(curve: list[tuple[int, float]], start_usd: float, target_ret: float) -> float | None:
    if not curve or start_usd <= 0:
        return None
    t0 = curve[0][0]
    hurdle = start_usd * (1.0 + target_ret)
    for ts, eq in curve:
        if eq >= hurdle:
            return (ts - t0) / 1e9 / 86400.0
    return None


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    rets = [r["ret_pct"] for r in rows]
    dds = [r["max_dd_pct"] for r in rows]
    return {
        "n": len(rows),
        "avg_ret": round(mean(rets), 2),
        "med_ret": round(median(rets), 2),
        "min_ret": round(min(rets), 2),
        "max_ret": round(max(rets), 2),
        "avg_dd": round(mean(dds), 2),
        "worst_dd": round(max(dds), 2),
        "pos": sum(1 for x in rets if x > 0),
        "open_max_max": max(r["open_max"] for r in rows),
        "avg_trades": round(mean(r["trades"] for r in rows), 1),
        "avg_closed": round(mean(r["closed"] for r in rows), 1),
    }


def load_cache(pairs: list[str], bar_minutes: int, data_start: str, data_end: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in pairs:
        bars = load_4h_bars(s, start=data_start, end=data_end, bar_minutes=bar_minutes)
        if bars:
            out[s] = bars
    return out


def run_one(cfg: dict, pairs: list[str], start: str, end: str, cache: dict) -> dict:
    r = run_portfolio_replay(
        pairs,
        strategy_cfg=cfg,
        start=start,
        end=end,
        start_usd=START_USD,
        fee_rate=FEE,
        bars_by_symbol=cache,
    )
    return {
        "start": start,
        "end": end,
        "equity": round(r.end_equity, 2),
        "ret_pct": round(r.net_return_pct * 100.0, 2),
        "max_dd_pct": round(r.max_dd_pct * 100.0, 2),
        "trades": r.trades,
        "wins": r.wins,
        "losses": r.losses,
        "closed": len(r.closed_trades),
        "open_n": r.open_positions,
        "open_max": r.open_max,
        "bars": r.bars,
        "warmup": r.data_start_warmup,
        "curve": r.equity_curve,
    }


def base_cfg() -> dict:
    return load_strategy_cfg(ROOT / "config" / "trend_rotation_25_max8_rotate.json")


def apply_variant(base: dict, overlay: dict) -> dict:
    cfg = deepcopy(base)
    cfg.update(overlay)
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="2026", choices=["2026", "full3m", "full6m"])
    ap.add_argument("--variants", default="", help="Comma names; default all (2026) or survivors")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    pairs = load_live_universe()
    base = base_cfg()
    wanted = [x.strip() for x in args.variants.split(",") if x.strip()]
    variants = [(n, o) for n, o in VARIANTS if not wanted or n in wanted]

    out_dir = Path(args.out) if args.out else ROOT / "results" / "backtests" / "horizon_compress"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.phase == "2026":
        windows = [("3m", a, b) for a, b in WINDOWS_3M] + [("6m", a, b) for a, b in WINDOWS_6M]
        data_end = "2026-09-02"
        # Warmest lookback among variants (1h ema 800 not in this grid; 4h ema 200 / 1h ema 200)
        # Longest warmup in this grid is 4h slow_ema=200 (~33d + pad), not 1h ema=200.
        dummy = apply_variant(base, {"bar_minutes": 240, "slow_ema_period": 200, "channel_period": 60})
        data_start = warmup_start(_dt("2026-01-02"), dummy).date().isoformat()
        tfs = sorted({int((apply_variant(base, o)).get("bar_minutes", 240) or 240) for _, o in variants})
        caches: dict[int, dict] = {}
        print(f"pairs={len(pairs)} data {data_start}→{data_end} tfs={tfs}", flush=True)
        for tf in tfs:
            t0 = time.time()
            caches[tf] = load_cache(pairs, tf, data_start, data_end)
            print(f"  loaded tf={tf} coins={len(caches[tf])} {time.time()-t0:.1f}s", flush=True)

        all_rows: list[dict] = []
        by_var: dict[str, dict[str, list[dict]]] = {}
        for name, overlay in variants:
            cfg = apply_variant(base, overlay)
            tf = int(cfg.get("bar_minutes", 240) or 240)
            cache = caches[tf]
            by_var[name] = {"3m": [], "6m": []}
            print(f"== {name} {overlay} ==", flush=True)
            for horizon, start, end in windows:
                t0 = time.time()
                row = run_one(cfg, pairs, start, end, cache)
                dt = time.time() - t0
                slim = {k: v for k, v in row.items() if k != "curve"}
                slim.update(
                    {
                        "variant": name,
                        "horizon": horizon,
                        "bar_minutes": tf,
                        "channel_period": cfg.get("channel_period"),
                        "slow_ema_period": cfg.get("slow_ema_period", 200),
                        "rotate_min_hold_bars": cfg.get("rotate_min_hold_bars"),
                        "max_concurrent": cfg.get("max_concurrent"),
                        "max_alloc_pct": cfg.get("max_alloc_pct"),
                        "atr_stop_mult": cfg.get("atr_stop_mult"),
                        "exit_mode": cfg.get("exit_mode"),
                        "secs": round(dt, 2),
                    }
                )
                by_var[name][horizon].append(slim)
                all_rows.append(slim)
                print(
                    f"  {horizon} {start}→{end} ret={slim['ret_pct']:+.1f}% dd={slim['max_dd_pct']:.1f} "
                    f"tr={slim['trades']} open_max={slim['open_max']} {dt:.1f}s",
                    flush=True,
                )

        score = []
        ssot3 = by_var["ssot_4h"]["3m"]
        ssot6 = by_var["ssot_4h"]["6m"]
        ssot6_by_start = {r["start"]: r["ret_pct"] for r in ssot6}
        ssot3_med = median(r["ret_pct"] for r in ssot3)
        ssot6_med = median(r["ret_pct"] for r in ssot6)
        for name, _ in variants:
            s3 = _summarize(by_var[name]["3m"])
            s6 = _summarize(by_var[name]["6m"])
            paired = []
            for birth, end3, end6 in PAIRED:
                v3 = next((r for r in by_var[name]["3m"] if r["start"] == birth), None)
                s6r = ssot6_by_start.get(birth)
                if v3 is None or s6r is None:
                    continue
                paired.append(
                    {
                        "birth": birth,
                        "var_3m": v3["ret_pct"],
                        "ssot_6m": s6r,
                        "ratio": None if s6r == 0 else round(v3["ret_pct"] / s6r, 3),
                        "gap_pp": round(v3["ret_pct"] - s6r, 2),
                    }
                )
            open_ok = s3.get("open_max_max", 99) <= 8
            # Promote screen: 3m median near SSOT 6m median, left tail not worse than SSOT 3m, slots honest
            med_ratio = None if ssot6_med == 0 else s3["med_ret"] / ssot6_med
            min_vs_ssot3 = s3["min_ret"] - min(r["ret_pct"] for r in ssot3)
            score.append(
                {
                    "variant": name,
                    "sum_3m": s3,
                    "sum_6m": s6,
                    "paired": paired,
                    "med3_vs_ssot6": round(s3["med_ret"] - ssot6_med, 2),
                    "med3_vs_ssot3": round(s3["med_ret"] - ssot3_med, 2),
                    "min3_vs_ssot3": round(min_vs_ssot3, 2),
                    "open_ok": open_ok,
                    "screen_pass": bool(
                        open_ok
                        and s3["med_ret"] >= ssot6_med * 0.8
                        and s3["min_ret"] >= min(r["ret_pct"] for r in ssot3) - 5.0
                    ),
                }
            )

        payload = {
            "engine": "signal_engine_cash_replay",
            "fee_bps": 40,
            "universe": "universe_live.json",
            "n_pairs": len(pairs),
            "ssot": {
                "strategy_id": "trend_rotation_25_max8_rotate",
                "bar_minutes": 240,
                "channel_period": 60,
                "slow_ema_period": 200,
                "exit_mode": "slow_ema",
                "atr_stop_mult": 0.0,
                "max_alloc_pct": 0.25,
                "max_concurrent": 8,
                "rotate_when_full": True,
                "rotate_min_hold_bars": 14,
            },
            "ssot3_med": ssot3_med,
            "ssot6_med": ssot6_med,
            "score": score,
            "rows": [{k: v for k, v in r.items() if k != "curve"} for r in all_rows],
        }
        outp = out_dir / "screen_2026.json"
        outp.write_text(json.dumps(payload, indent=2))
        print("\n=== 2026 SCREEN ===", flush=True)
        print(f"SSOT 3m med={ssot3_med:+.1f}%  6m med={ssot6_med:+.1f}%", flush=True)
        for s in score:
            flag = "PASS" if s["screen_pass"] else "fail"
            print(
                f"{flag:4} {s['variant']:22} 3m med={s['sum_3m']['med_ret']:+7.1f} min={s['sum_3m']['min_ret']:+7.1f} "
                f"vs6m={s['med3_vs_ssot6']:+6.1f} vs3m={s['med3_vs_ssot3']:+6.1f} "
                f"open_ok={s['open_ok']} trades~{s['sum_3m']['avg_trades']}",
                flush=True,
            )
        print(f"Wrote {outp}", flush=True)
        return

    # Full graduated fixed-horizon backtests
    end_cap = datetime(2026, 9, 2).date()
    months = 3 if args.phase == "full3m" else 6
    starts = []
    cur = datetime(2023, 7, 2).date()
    while True:
        e = cur + relativedelta(months=months)
        if e > end_cap:
            break
        starts.append((cur.isoformat(), e.isoformat()))
        cur += relativedelta(months=1)
    print(f"full {months}m windows={len(starts)} {starts[0]} … {starts[-1]}", flush=True)

    dummy = apply_variant(base, {"bar_minutes": 240, "slow_ema_period": 200})
    data_start = warmup_start(_dt(starts[0][0]), dummy).date().isoformat()
    tfs = sorted({int((apply_variant(base, o)).get("bar_minutes", 240) or 240) for _, o in variants})
    caches = {}
    for tf in tfs:
        t0 = time.time()
        caches[tf] = load_cache(pairs, tf, data_start, end_cap.isoformat())
        print(f"  loaded tf={tf} coins={len(caches[tf])} {time.time()-t0:.1f}s", flush=True)

    payload_rows = []
    for name, overlay in variants:
        cfg = apply_variant(base, overlay)
        tf = int(cfg.get("bar_minutes", 240) or 240)
        rows = []
        print(f"== {name} ==", flush=True)
        for start, end in starts:
            t0 = time.time()
            row = run_one(cfg, pairs, start, end, caches[tf])
            slim = {k: v for k, v in row.items() if k != "curve"}
            slim["variant"] = name
            rows.append(slim)
            print(
                f"  {start}→{end} {slim['ret_pct']:+6.1f}% dd={slim['max_dd_pct']:.1f} "
                f"om={slim['open_max']} {time.time()-t0:.1f}s",
                flush=True,
            )
        summ = _summarize(rows)
        print(f"  SUM {name} {summ}", flush=True)
        payload_rows.append({"variant": name, "overlay": overlay, "summary": summ, "rows": rows})

    outp = out_dir / f"{args.phase}.json"
    outp.write_text(
        json.dumps(
            {
                "phase": args.phase,
                "months": months,
                "fee_bps": 40,
                "universe": "universe_live.json",
                "n_windows": len(starts),
                "variants": payload_rows,
            },
            indent=2,
        )
    )
    print(f"Wrote {outp}", flush=True)


if __name__ == "__main__":
    main()
