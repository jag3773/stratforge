#!/usr/bin/env python3
"""Full 3m/6m for one-factor SSOT knobs that lifted 2026 without wrecking min."""
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
VARIANTS = [
    ("ssot", {}),
    ("ch48", {"channel_period": 48}),
    ("ch42", {"channel_period": 42}),
    ("ema220", {"slow_ema_period": 220}),
    ("cd3", {"cooldown_bars": 3}),
    ("nofilter", {"trend_filter": False}),
    ("max10", {"max_concurrent": 10}),
    ("alloc30", {"max_alloc_pct": 0.30}),
    ("alloc35", {"max_alloc_pct": 0.35}),
    ("alloc40", {"max_alloc_pct": 0.40}),
    ("buf_p1", {"slow_ema_buffer": 1.0}),
    ("ch48_alloc30", {"channel_period": 48, "max_alloc_pct": 0.30}),
    ("ch48_max10", {"channel_period": 48, "max_concurrent": 10}),
    ("alloc35_max10", {"max_alloc_pct": 0.35, "max_concurrent": 10}),
    ("ch48_alloc35_cd3", {"channel_period": 48, "max_alloc_pct": 0.35, "cooldown_bars": 3}),
]


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def windows(months: int):
    end = datetime(2026, 9, 2).date()
    out = []
    cur = datetime(2023, 7, 2).date()
    while True:
        e = cur + relativedelta(months=months)
        if e > end:
            break
        out.append((cur.isoformat(), e.isoformat()))
        cur += relativedelta(months=1)
    return out


def main() -> None:
    pairs = load_live_universe()
    base = load_strategy_cfg(ROOT / "config" / "trend_rotation_25_max8_rotate.json")
    data_start = warmup_start(dt("2023-07-02"), base).date().isoformat()
    print("cache", data_start, flush=True)
    t0 = time.time()
    cache = {}
    for s in pairs:
        b = load_4h_bars(s, start=data_start, end="2026-09-02", bar_minutes=240)
        if b:
            cache[s] = b
    print(f"cached {len(cache)} {time.time()-t0:.1f}s", flush=True)

    def run_many(name, ov, wins):
        cfg = deepcopy(base)
        cfg.update(ov)
        cap = int(cfg.get("max_concurrent", 8) or 8)
        rows = []
        for a, b in wins:
            r = run_portfolio_replay(
                pairs,
                strategy_cfg=cfg,
                start=a,
                end=b,
                start_usd=1000,
                fee_rate=FEE,
                bars_by_symbol=cache,
            )
            rows.append(
                dict(
                    start=a,
                    end=b,
                    ret=round(r.net_return_pct * 100, 2),
                    dd=round(r.max_dd_pct * 100, 2),
                    trades=r.trades,
                    open_max=r.open_max,
                    open_ok=r.open_max <= cap,
                )
            )
        rets = [x["ret"] for x in rows]
        return dict(
            variant=name,
            overlay=ov,
            n=len(rows),
            avg=round(mean(rets), 2),
            med=round(median(rets), 2),
            mn=round(min(rets), 2),
            mx=round(max(rets), 2),
            pos=sum(x > 0 for x in rets),
            worst_dd=round(max(x["dd"] for x in rows), 1),
            open_max_max=max(x["open_max"] for x in rows),
            open_ok=all(x["open_ok"] for x in rows),
            avg_trades=round(mean(x["trades"] for x in rows), 1),
            p10=round(sorted(rets)[max(0, int(0.1 * len(rets)) - 1)], 2),
            rows=rows,
        )

    out = {}
    for months, key in ((3, "3m"), (6, "6m")):
        wins = windows(months)
        print(f"\n=== {key} n={len(wins)} ===", flush=True)
        block = []
        for name, ov in VARIANTS:
            rec = run_many(name, ov, wins)
            block.append(rec)
            print(
                f"{name:22} avg={rec['avg']:+6.1f} med={rec['med']:+6.1f} min={rec['mn']:+6.1f} "
                f"p10={rec['p10']:+6.1f} pos={rec['pos']}/{rec['n']} om={rec['open_max_max']} ok={rec['open_ok']}",
                flush=True,
            )
        out[key] = block

    ssot3 = {r["start"]: r["ret"] for r in out["3m"][0]["rows"]}
    ssot6 = {r["start"]: r["ret"] for r in out["6m"][0]["rows"]}
    print("\n=== pairwise ===", flush=True)
    for rec in out["3m"]:
        g3 = []
        g6 = []
        b3 = 0
        vs6 = 0
        for r in rec["rows"]:
            g3.append(r["ret"] - ssot3[r["start"]])
            if r["ret"] > ssot3[r["start"]]:
                b3 += 1
            if r["start"] in ssot6:
                g6.append(r["ret"] - ssot6[r["start"]])
                if r["ret"] >= ssot6[r["start"]]:
                    vs6 += 1
        rec["beat_ssot3"] = f"{b3}/{len(rec['rows'])}"
        rec["med_gap3"] = round(median(g3), 2)
        rec["beat_ssot6_samebirth"] = f"{vs6}/{len(g6)}"
        rec["med_gap6"] = round(median(g6), 2) if g6 else None
        print(
            f"{rec['variant']:22} beat3 {rec['beat_ssot3']} med_gap3={rec['med_gap3']:+.1f}  "
            f"3m>=ssot6 {rec['beat_ssot6_samebirth']} med_gap6={rec['med_gap6']:+.1f}",
            flush=True,
        )

    path = ROOT / "results" / "backtests" / "horizon_compress" / "ssot_knob_full_onefactor.json"
    path.write_text(json.dumps(out, indent=2))
    print("wrote", path, flush=True)


if __name__ == "__main__":
    main()
