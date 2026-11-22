#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfm.venue import load_venue, multiplier


def test_load_venue_btc_nano() -> None:
    v = load_venue()
    assert v["venue"] == "CFM"
    assert v["contracts"]["BTC"]["root"] == "BIP"
    assert abs(multiplier("BTC", v) - 0.01) < 1e-12


def test_research_leverage_is_four_overnight() -> None:
    v = load_venue()
    assert float(v["research_leverage_overnight"]) == 4.0
    assert float(v["max_advertised_intraday_leverage"]) == 10.0


def test_out_of_scope_includes_live_and_spot_dna() -> None:
    v = load_venue()
    blob = " ".join(v["out_of_scope"])
    assert "live orders" in blob
    assert "trend_rotation_25_max8_rotate" in blob


if __name__ == "__main__":
    test_load_venue_btc_nano()
    test_research_leverage_is_four_overnight()
    test_out_of_scope_includes_live_and_spot_dna()
    print("ok venue")
