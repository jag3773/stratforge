from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stratforge.state import state_root

def _root() -> Path:
    return state_root()


def _default_venue() -> Path:
    return _root() / "config" / "cfm" / "venue.json"


def load_venue(path: str | Path | None = None) -> dict[str, Any]:
    root = _root()
    p = Path(path) if path else _default_venue()
    if not p.is_absolute():
        p = root / p
    raw = json.loads(p.read_text())
    return {k: v for k, v in raw.items() if not str(k).startswith("_") and k != "comment"}


def multiplier(symbol: str, venue: dict | None = None) -> float:
    v = venue or load_venue()
    c = (v.get("contracts") or {}).get(symbol.upper()) or {}
    return float(c.get("multiplier_coins") or 0.0)


def load_cde_universe() -> dict[str, Any]:
    p = _root() / "config" / "cfm" / "universe_cde.json"
    raw = json.loads(p.read_text())
    return raw.get("symbols") or {}


def cde_multipliers() -> dict[str, float]:
    return {k.upper(): float(v["multiplier"]) for k, v in load_cde_universe().items()}


def cde_symbols() -> list[str]:
    return sorted(load_cde_universe().keys())
