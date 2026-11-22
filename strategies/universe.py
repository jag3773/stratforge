"""
Universe SSOT loader.

LIVE paper/live_node reads only admitted symbols (config/universe_live.json).
Watchlist / discovery lives under data/admission/ and is NEVER imported here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_PATH = ROOT / "config" / "universe_live.json"

# Hard fallback if JSON missing (bootstrap safety; promote path keeps JSON current).
_FALLBACK = [
    "AAVE", "ADA", "AERO", "ALLO", "ASTER", "AVAX", "BCH", "BNB", "BONK", "BTC", "CRO",
    "DOGE", "DOT", "ETC", "ETH", "HBAR", "HIGH", "HYPE", "ICP", "INJ", "JASMY", "KAITO",
    "LINK", "LTC", "MANTLE", "MORPHO", "NEAR", "NKN", "ONDO", "OXT", "PAXG", "PENGU",
    "PEPE", "POL", "PUMP", "SHIB", "SKY", "SOL", "SUI", "TAO", "TON", "TRAC", "UNI",
    "USELESS", "VVV", "WLD", "WLFI", "XLM", "XRP", "ZEC",
]


def _load_symbols(path: Path) -> list[str]:
    if not path.is_file():
        return list(_FALLBACK)
    data = json.loads(path.read_text())
    syms = [str(s).strip().upper() for s in data.get("symbols") or [] if str(s).strip()]
    # unique preserve order
    out: list[str] = []
    seen = set()
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out or list(_FALLBACK)


def load_live_universe(path: str | Path | None = None) -> list[str]:
    """Admitted production universe only."""
    p = Path(path) if path else Path(os.getenv("UNIVERSE_LIVE_PATH", str(LIVE_PATH)))
    return _load_symbols(p)


def live_universe_meta(path: str | Path | None = None) -> dict:
    p = Path(path) if path else LIVE_PATH
    if not p.is_file():
        return {"symbols": load_live_universe(), "path": str(p), "missing": True}
    data = json.loads(p.read_text())
    data["path"] = str(p)
    data["count"] = len(data.get("symbols") or [])
    return data


# Convenience alias used by older call sites
UNIVERSE = load_live_universe()
