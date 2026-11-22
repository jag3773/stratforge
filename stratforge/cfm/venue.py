from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENUE = ROOT / "config" / "cfm" / "venue.json"


def load_venue(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_VENUE
    if not p.is_absolute():
        p = ROOT / p
    raw = json.loads(p.read_text())
    return {k: v for k, v in raw.items() if not str(k).startswith("_") and k != "comment"}


def multiplier(symbol: str, venue: dict | None = None) -> float:
    v = venue or load_venue()
    c = (v.get("contracts") or {}).get(symbol.upper()) or {}
    return float(c.get("multiplier_coins") or 0.0)


def load_cde_universe() -> dict[str, Any]:
    p = ROOT / "config" / "cfm" / "universe_cde.json"
    raw = json.loads(p.read_text())
    return raw.get("symbols") or {}


def cde_multipliers() -> dict[str, float]:
    return {k.upper(): float(v["multiplier"]) for k, v in load_cde_universe().items()}


def cde_symbols() -> list[str]:
    return sorted(load_cde_universe().keys())
