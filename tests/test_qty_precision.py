"""Quantity floor / sell clip. No network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.qty_precision import clip_sell_qty, floor_qty


def test_clip_sell_qty_xlm_rounded_book_does_not_exceed_wallet():
    # 2026-09-17 LIVE XLM close: BOOK_SYNC rounded 1620.94349082 up to 1620.9435
    requested = 1620.9435
    available = 1620.94349082
    qty = clip_sell_qty(requested, available)
    assert qty <= available
    assert qty == available
    print("ok xlm clip")


def test_clip_sell_qty_equal_passthrough():
    assert clip_sell_qty(10.5, 10.5) == 10.5
    print("ok equal")


def test_clip_sell_qty_zero_or_missing_available():
    assert clip_sell_qty(10.0, 0.0) == 0.0
    assert clip_sell_qty(10.0, -1.0) == 0.0
    print("ok zero available")


def test_floor_qty_never_rounds_up():
    assert floor_qty(1.239, decimals=2) == 1.23
    print("ok floor")


if __name__ == "__main__":
    test_clip_sell_qty_xlm_rounded_book_does_not_exceed_wallet()
    test_clip_sell_qty_equal_passthrough()
    test_clip_sell_qty_zero_or_missing_available()
    test_floor_qty_never_rounds_up()
    print("ALL PASS")
