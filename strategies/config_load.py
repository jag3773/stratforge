"""Resolve JSON configs with `_include` (DNA inherit).

`_parent` is documentation-only lineage and is never merged — the production
parent (`trend_rotation_25pct.json`) is a different DNA (max50, no rotate).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
INCLUDE_KEY = "_include"

# Signal/DNA knobs that belong in the strategy JSON, not live overlays.
# Indicator periods (rsi/ema/adx/atr) stay as SignalConfig.from_dict defaults.
DNA_KEYS = (
    "mode",
    "bar_minutes",
    "channel_period",
    "atr_stop_mult",
    "cooldown_bars",
    "trend_filter",
    "long_only",
    "exit_mode",
    "slow_ema_buffer",
    "max_concurrent",
    "max_alloc_pct",
    "rotate_when_full",
    "rotate_min_hold_bars",
    "risk_percentage",
    "btc_regime_filter",
    "cat_stop_pct",
    "account_type",
)

DNA_INCLUDE = "config/trend_rotation_25_max8_rotate.json"


def resolve_path(path: str | Path, *, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else REPO_ROOT
    p = Path(path)
    return p if p.is_absolute() else base / p


def load_config(
    path: str | Path,
    *,
    root: Path | None = None,
    _stack: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Load JSON, merge `_include` first, then overlay local keys."""
    base = Path(root) if root is not None else REPO_ROOT
    p = resolve_path(path, root=base)
    key = str(p.resolve())
    if key in _stack:
        chain = " -> ".join(_stack + (key,))
        raise ValueError(f"config include cycle: {chain}")
    raw = json.loads(p.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: expected JSON object")
    include = raw.get(INCLUDE_KEY)
    merged: dict[str, Any] = {}
    included_from: str | None = None
    if include:
        inc_path = resolve_path(str(include), root=base)
        included_from = str(inc_path)
        merged = load_config(inc_path, root=base, _stack=_stack + (key,))
    for k, v in raw.items():
        if k == INCLUDE_KEY:
            continue
        merged[k] = v
    merged["_config_path"] = str(p)
    if included_from:
        merged["_included"] = included_from
    return merged


def copied_dna_keys(raw: dict) -> list[str]:
    """DNA knobs re-declared on a live overlay (drift risk)."""
    return [k for k in DNA_KEYS if k in raw]
