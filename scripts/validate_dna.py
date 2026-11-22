#!/usr/bin/env python3
"""Assert production DNA knobs match across strategy and resolved live configs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.config_load import (  # noqa: E402
    DNA_INCLUDE,
    DNA_KEYS,
    INCLUDE_KEY,
    copied_dna_keys,
    load_config,
)

PROD = {
    "strategy_id": "trend_rotation_25_max8_rotate",
    "mode": "breakout",
    "channel_period": 60,
    "exit_mode": "slow_ema",
    "atr_stop_mult": 0.0,
    "max_alloc_pct": 0.25,
    "max_concurrent": 8,
    "rotate_when_full": True,
    "rotate_min_hold_bars": 14,
}
SSOT = "config/trend_rotation_25_max8_rotate.json"
LIVE_FILES = (
    "config/live_paper.json",
    "config/live_production.json",
    "config/live_micro.json",
)


def _check_knobs(rel: str, d: dict) -> list[str]:
    errors: list[str] = []
    for k, v in PROD.items():
        got = d.get(k)
        if k == "rotate_when_full":
            got = bool(got)
        if got != v:
            errors.append(f"{rel}: {k}={got!r} expected {v!r}")
    acct = str(d.get("account_type", "CASH")).upper()
    if acct != "CASH":
        errors.append(f"{rel}: account_type={acct!r} expected 'CASH'")
    return errors


def validate(root: Path | None = None) -> list[str]:
    base = root or ROOT
    errors: list[str] = []
    ssot = base / SSOT
    if not ssot.is_file():
        return [f"missing {SSOT}"]
    errors.extend(_check_knobs(SSOT, json.loads(ssot.read_text())))
    for rel in LIVE_FILES:
        path = base / rel
        if not path.is_file():
            errors.append(f"missing {rel}")
            continue
        raw = json.loads(path.read_text())
        inc = raw.get(INCLUDE_KEY)
        if inc != DNA_INCLUDE:
            errors.append(f"{rel}: {INCLUDE_KEY}={inc!r} expected {DNA_INCLUDE!r}")
        copied = copied_dna_keys(raw)
        if copied:
            errors.append(f"{rel}: recopied DNA knobs {copied} — inherit via {INCLUDE_KEY}")
        sid = raw.get("strategy_id")
        if sid and sid != PROD["strategy_id"]:
            errors.append(f"{rel}: strategy_id={sid!r} expected {PROD['strategy_id']!r}")
        resolved = load_config(path, root=base)
        errors.extend(_check_knobs(f"{rel} (resolved)", resolved))
        # Guard: every DNA key must actually arrive from include, not defaults.
        missing = [k for k in DNA_KEYS if k not in resolved]
        if missing:
            errors.append(f"{rel}: resolved missing {missing}")
    return errors


def main() -> int:
    errs = validate()
    if errs:
        print("DNA JSON FAIL", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("DNA JSON OK (include + resolved knobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
