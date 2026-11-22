#!/usr/bin/env python3
"""Pure buy-protect decision helper (no exchange I/O)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.buy_protect import BuyProtectDecision, decide_buy  # noqa: E402


def test_tight_book_takes_at_ask():
    # bid/ask give ~20 bps spread (<40). close=100.1 so ask is inside the 15 bps chase cap
    # (ask 100.2 vs close*1.0015 ≈ 100.250). close=100 would trip chase (100.2 > 100.15).
    d = decide_buy(100.0, 100.2, 100.1)
    assert d.skip is False
    assert d.reason is None
    assert d.limit_price == 100.2
    assert d.bid == 100.0
    assert d.ask == 100.2
    assert d.spread_bps is not None
    assert abs(d.spread_bps - ((100.2 - 100.0) / 100.1 * 1e4)) < 1e-9
    assert isinstance(d, BuyProtectDecision)


def test_wide_book_skips_spread():
    # mid=100.25, (0.5/100.25)*1e4 ≈ 49.88 bps > 40
    d = decide_buy(100.0, 100.5, 100.0)
    assert d.skip is True
    assert d.reason == "spread"
    assert d.limit_price is None
    assert d.bid == 100.0
    assert d.ask == 100.5
    assert d.spread_bps is not None
    assert d.spread_bps > 40.0


def test_ask_above_chase_cap_skips_chase():
    # close=100 → cap ask 100.15; ask 100.16 with a tight book
    d = decide_buy(100.14, 100.16, 100.0)
    assert d.skip is True
    assert d.reason == "chase"
    assert d.limit_price is None
    assert d.bid == 100.14
    assert d.ask == 100.16
    assert d.spread_bps is not None


def test_ask_below_close_still_takes_at_ask():
    d = decide_buy(99.8, 99.9, 100.0)
    assert d.skip is False
    assert d.reason is None
    assert d.limit_price == 99.9
    assert d.bid == 99.8
    assert d.ask == 99.9


def test_ask_exactly_at_chase_cap_takes():
    d = decide_buy(100.0, 100.15, 100.0)
    assert d.skip is False
    assert d.reason is None
    assert d.limit_price == 100.15
    assert d.ask == 100.15


def test_none_or_zero_book_skips_book():
    for bid, ask in (
        (None, 100.0),
        (100.0, None),
        (None, None),
        (0.0, 100.0),
        (100.0, 0.0),
        (0.0, 0.0),
        (-1.0, 100.0),
        (100.0, -1.0),
    ):
        d = decide_buy(bid, ask, 100.0)
        assert d.skip is True, (bid, ask)
        assert d.reason == "book"
        assert d.limit_price is None
        assert d.spread_bps is None


def test_non_positive_close_skips_book():
    d = decide_buy(100.0, 100.2, 0.0)
    assert d.skip is True
    assert d.reason == "book"
    assert d.limit_price is None
    d = decide_buy(100.0, 100.2, -1.0)
    assert d.skip is True
    assert d.reason == "book"
    assert d.limit_price is None


def test_ask_below_bid_skips_book():
    d = decide_buy(100.2, 100.0, 100.0)
    assert d.skip is True
    assert d.reason == "book"
    assert d.limit_price is None
    assert d.spread_bps is None
    assert d.bid == 100.2
    assert d.ask == 100.0


def test_non_finite_bid_ask_close_skips_book():
    for bid, ask, close in (
        (math.nan, 100.2, 100.1),
        (100.0, math.nan, 100.1),
        (100.0, 100.2, math.nan),
        (math.inf, 100.2, 100.1),
        (100.0, math.inf, 100.1),
        (100.0, 100.2, math.inf),
    ):
        d = decide_buy(bid, ask, close)
        assert d.skip is True, (bid, ask, close)
        assert d.reason == "book", (bid, ask, close)
        assert d.limit_price is None, (bid, ask, close)
