#!/usr/bin/env python3
"""Universe loader used by the live node."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_universe_loader():
    from strategies.universe import LIVE_PATH, load_live_universe

    u = load_live_universe()
    assert len(u) >= 40
    assert "BTC" in u and "ETH" in u
    assert LIVE_PATH.is_file()
    from strategies.live_node import UNIVERSE

    assert UNIVERSE == u


if __name__ == "__main__":
    test_universe_loader()
    print("ALL ADMISSION TESTS PASS")
