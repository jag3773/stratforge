#!/usr/bin/env python3
"""
Score baseline bear_regime_cover vs slower-regime gates on the same windows.

  # run from the checkout
  .venv/bin/python short/run_regime_gates.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG))
from stratforge.state import state_root  # noqa: E402

from short.run_backtest import run_one  # noqa: E402


WINDOWS = [
    ("2021-11-01", "2022-12-31", "bear2022"),
    ("2023-07-18", "2026-07-18", "long_window"),
    ("2024-03-01", "2024-09-01", "dd_2024"),
    ("2025-10-01", "2026-07-15", "dd_2025_26"),
    ("2020-07-01", "2026-08-13", "full"),
]

CONFIGS = [
    "config/short/bear_regime_cover.json",
    "config/short/bear_regime_ema_falling.json",
    "config/short/bear_regime_persist12.json",
    "config/short/bear_regime_daily200.json",
]


class _Args:
    def __init__(self, config: str):
        self.config = config
        self.start_usd = 10_000.0
        self.fee = 0.0004
        self.funding = 0.00003
        self.integer_contracts = False


def main() -> int:
    symbols = ["BTC", "ETH"]
    by_cfg: dict[str, list[dict]] = {}
    for cfg in CONFIGS:
        args = _Args(cfg)
        rows = []
        for start, end, label in WINDOWS:
            out = run_one(args, start, end, label, symbols)
            sid = out.get("strategy_id")
            print(
                f"{sid:28} {label:14} ret={out['net_return_pct']:+7.2f}% "
                f"dd={out['max_dd_pct']:5.1f}% trades={out['trades']:3d} "
                f"bull={((out.get('regime') or {}).get('bull_return_pct') or 0):+.1f}%"
            )
            rows.append(out)
        by_cfg[cfg] = rows
        print()

    payload = {
        "scored_at": "regime_gate_compare",
        "start_usd": 10000.0,
        "fee_rate": 0.0004,
        "funding_per_8h": 0.00003,
        "windows": [w[2] for w in WINDOWS],
        "configs": CONFIGS,
        "results": {
            cfg: [
                {
                    "label": r["label"],
                    "strategy_id": r["strategy_id"],
                    "net_return_pct": r["net_return_pct"],
                    "max_dd_pct": r["max_dd_pct"],
                    "trades": r["trades"],
                    "wins": r["wins"],
                    "losses": r["losses"],
                    "fees_paid": r["fees_paid"],
                    "funding_paid": r["funding_paid"],
                    "regime": r.get("regime") or {},
                    "beats_cash": r.get("beats_cash"),
                    "btc_buyhold_return_pct": (r.get("benchmarks") or {}).get(
                        "btc_buyhold_return_pct"
                    ),
                    "btc_shorthold_return_pct": (r.get("benchmarks") or {}).get(
                        "btc_shorthold_return_pct"
                    ),
                }
                for r in rows
            ]
            for cfg, rows in by_cfg.items()
        },
    }
    out_path = state_root() / "results" / "backtests" / "short" / "regime_gate_compare.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
