#!/usr/bin/env python3
"""Build results/backtests/README.md from committed rollup JSONs."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1] / "results" / "backtests"


def load_json(rel: str):
    p = ROOT / rel
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def stats_from_rows(rows):
    if not rows or not isinstance(rows, list):
        return None
    rets = []
    beats = 0
    open_max = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if "return_pct" in r:
            rets.append(float(r["return_pct"]))
        elif "strategy_return_pct" in r:
            rets.append(float(r["strategy_return_pct"]))
        if r.get("beats_btc"):
            beats += 1
        for k in ("open_n", "open_max"):
            if k in r and r[k] is not None:
                om = int(r[k])
                open_max = om if open_max is None else max(open_max, om)
    if not rets:
        return {"n": len(rows)}
    return {
        "n": len(rets),
        "avg_ret": round(mean(rets), 2),
        "med_ret": round(median(rets), 2),
        "min_ret": round(min(rets), 2),
        "max_ret": round(max(rets), 2),
        "beats_btc": beats,
        "open_max_obs": open_max,
    }


def fmt_stats(s):
    if not s:
        return "_no numeric summary_"
    if "avg_ret" not in s:
        return f"n={s.get('n')}"
    out = (
        f"n={s['n']} | avg={s['avg_ret']:+.1f}% | med={s['med_ret']:+.1f}% | "
        f"min={s['min_ret']:+.1f}% | max={s['max_ret']:+.1f}% | "
        f"beats_btc={s.get('beats_btc')}/{s['n']}"
    )
    if s.get("open_max_obs") is not None:
        out += f" | open_max_obs={s['open_max_obs']}"
    return out


def main() -> None:
    sections = [
        (
            "monthly_history_max8_rotate",
            "PRODUCTION DNA / leading candidate",
            "config/trend_rotation_25_max8_rotate.json (max_alloc=0.25, max_concurrent=8, rotate_when_full, hold=14)",
            "CASH monthly births ~2023-07→2026-07 → end 2026-08-02",
            "monthly_history_max8_rotate/rollup.json",
        ),
        (
            "monthly_history",
            "Research baseline max50 no-rotate",
            "config/trend_rotation_25pct.json / trend_rotation_25_baseline_max50.json",
            "CASH monthly births",
            "monthly_history/rollup.json",
        ),
        (
            "monthly_history_max4",
            "REJECTED — max_concurrent=4 only",
            "max4 inventory experiment",
            "CASH — inventory fix without rotation edge",
            "monthly_history_max4/rollup.json",
        ),
        (
            "defensive",
            "REJECTED — BTC regime + cat stop + max4",
            "archive/config defensive stack",
            "CASH — fixed left tail, killed right tail (user rejected)",
            "defensive/rollup.json",
        ),
        (
            "legacy_levered_margin",
            "INVALID for spot claims",
            "legacy MARGIN default ~10x",
            "Do not use for spot economics",
            "legacy_levered_margin/rollup.json",
        ),
        (
            "recent_windows",
            "Short recent staggered windows (m01–m12)",
            "CASH rollup of recent births ending ~2026-08-02",
            "Includes per-window JSON snapshots",
            "recent_windows/rollup_cash.json",
        ),
    ]

    lines = [
        "# Backtest summaries (committed)",
        "",
        "**Status:** Compact **rollups / summaries** checked into git.",
        "Full multi-GB stress artifacts stay local under `data/staggered_starts/` (gitignored).",
        "",
        "**Engine note:** These historical cohorts were produced with the Nautilus 1.221 CASH harness",
        "(`archive/strategies_v1_backtest/`). Current research path is `strategies/research_replay.py`",
        "on `.venv` (Phase 0). Figures remain the evidence base for strategy selection.",
        "",
        "**Production strategy:** `trend_rotation_25_max8_rotate`",
        "(`max_alloc_pct=0.25`, `max_concurrent=8`, `rotate_when_full=true`,",
        "`rotate_min_hold_bars=14`, CASH spot).",
        "",
        "## Index",
        "",
        "| Cohort | Role | Path |",
        "|---|---|---|",
    ]
    for name, role, _cfg, _note, path in sections:
        lines.append(f"| `{name}` | {role} | [`{path}`]({path}) |")
    lines.append(
        "| `stress_max8_rotate` | Hold/fee stress on max8+rotate | "
        "[`stress_max8_rotate/stress_summary.json`](stress_max8_rotate/stress_summary.json) |"
    )
    lines.append("")
    lines.append("## Headline stats")
    lines.append("")

    for name, role, cfg, note, path in sections:
        rows = load_json(path)
        st = stats_from_rows(rows if isinstance(rows, list) else None)
        lines += [
            f"### `{name}`",
            f"- **Role:** {role}",
            f"- **Config / knobs:** {cfg}",
            f"- **Notes:** {note}",
            f"- **Summary:** {fmt_stats(st)}",
            f"- **File:** [`{path}`]({path})",
            "",
        ]

    stress = load_json("stress_max8_rotate/stress_summary.json")
    lines += [
        "### `stress_max8_rotate`",
        "- **Role:** Hold bars + taker fee sensitivity on max8+rotate",
        "- **Files:** `stress_summary.json`, `hold_sweep_rollup.json`, `fee_sweep_rollup.json`",
    ]
    if isinstance(stress, dict) and stress.get("hold_sweep"):
        lines += [
            "",
            "- **Hold sweep (40 bps taker):**",
            "",
            "| hold | avg_ret% | med_ret% | min | max | beats_btc | open_max | m10_ret% |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in stress["hold_sweep"]:
            lines.append(
                f"| {row.get('hold')} | {row.get('avg_ret')} | {row.get('med_ret')} | "
                f"{row.get('min_ret')} | {row.get('max_ret')} | "
                f"{row.get('beats_btc')}/{row.get('n')} | {row.get('open_max')} | {row.get('m10_ret')} |"
            )
    if isinstance(stress, dict) and stress.get("fee_sweep"):
        lines += [
            "",
            "- **Fee sweep:**",
            "",
            "| taker_bps | avg_ret% | med_ret% | min | max | beats_btc |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
        for row in stress["fee_sweep"]:
            lines.append(
                f"| {row.get('taker_bps')} | {row.get('avg_ret')} | {row.get('med_ret')} | "
                f"{row.get('min_ret')} | {row.get('max_ret')} | "
                f"{row.get('beats_btc')}/{row.get('n')} |"
            )

    lines += [
        "",
        "## Rejected / invalid (do not promote)",
        "",
        "- **defensive** (regime+cat+max4): left-tail fix, right-tail destroyed — user rejected.",
        "- **monthly_history_max4**: inventory control without rotation edge — growth fail.",
        "- **legacy_levered_margin**: not spot-honest.",
        "",
        "## Reproduce (current runtime)",
        "",
        "```bash",
        "# run from the checkout",
        ".venv/bin/python scripts/staggered_backtest_status.py \\",
        "  --start 2026-07-02 --end 2026-08-02 --label m01 \\",
        "  --config config/trend_rotation_25_max8_rotate.json",
        ".venv/bin/python scripts/run_portfolio_backtest.py \\",
        "  --config config/trend_rotation_25_max8_rotate.json \\",
        "  --start 2023-07-18 --end 2026-07-18",
        "```",
        "",
        "When new rollups appear under `data/staggered_starts/`, copy compact",
        "`rollup*.json` / `*summary*.json` into this tree and regenerate this README:",
        "",
        "```bash",
        ".venv/bin/python scripts/build_backtest_results_readme.py",
        "```",
        "",
    ]
    out = ROOT / "README.md"
    out.write_text("\n".join(lines))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
