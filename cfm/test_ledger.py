#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfm.ledger import FuturesLedger


def test_open_long_posts_margin_and_fee() -> None:
    L = FuturesLedger(start_usd=10_000, max_leverage=4.0, fee_rate=0.001, integer_contracts=False)
    rec = L.open_pos("BTC", "LONG", 100_000, 1, target_notional=5_000, reason="t")
    assert rec is not None
    assert abs(rec["qty"] - 0.05) < 1e-9
    assert abs(L.cash - (10_000 - 5.0)) < 1e-6
    assert abs(rec["margin"] - 1250.0) < 1e-6
    assert abs(L.equity() - (L.cash + 0.0)) < 1e-6


def test_long_unrealized_and_close_pnl() -> None:
    L = FuturesLedger(start_usd=10_000, max_leverage=4.0, fee_rate=0.0, integer_contracts=False)
    L.open_pos("BTC", "LONG", 100_000, 1, 5_000, "t")
    L.update_price("BTC", 110_000)
    assert abs(L.unrealized() - 500.0) < 1e-6
    rec = L.close("BTC", 110_000, 2, "x")
    assert rec is not None
    assert abs(rec["pnl"] - 500.0) < 1e-6


def test_integer_bip_floors_qty() -> None:
    L = FuturesLedger(start_usd=10_000, max_leverage=4.0, fee_rate=0.0, integer_contracts=True)
    rec = L.open_pos("BTC", "LONG", 100_000, 1, 3_000, "t")  # 0.03 BTC → 3 BIP
    assert rec is not None
    assert abs(rec["qty"] - 0.03) < 1e-12
    rec2 = L.open_pos("ETH", "LONG", 2_000, 1, 150, "t")  # 0.075 ETH → 0 lots of 0.10
    assert rec2 is None


def test_funding_debits_never_credits() -> None:
    L = FuturesLedger(start_usd=10_000, max_leverage=4.0, fee_rate=0.0, funding_per_8h=0.00003, integer_contracts=False)
    L.open_pos("BTC", "LONG", 100_000, 0, 5_000, "t")
    L.update_price("BTC", 100_000, 1)
    L.last_funding_ts = 1
    later = 1 + 16 * 3600 * 1_000_000_000
    L.apply_funding(later)
    assert L.funding_paid > 0
    assert L.cash < 10_000


def test_liq_flattens() -> None:
    L = FuturesLedger(start_usd=1_000, max_leverage=4.0, fee_rate=0.0, integer_contracts=False)
    L.open_pos("BTC", "LONG", 100_000, 1, 4_000, "t")
    rec = L.maybe_liquidate_bar("BTC", high=100_000, low=10_000, ts=2)
    assert rec is not None
    assert rec.get("liquidation")
    assert "BTC" not in L.lots


def test_refuses_leverage_gt_4() -> None:
    try:
        FuturesLedger(start_usd=1000, max_leverage=10.0)
    except ValueError:
        return
    raise AssertionError("should refuse 10x")


if __name__ == "__main__":
    test_open_long_posts_margin_and_fee()
    test_long_unrealized_and_close_pnl()
    test_integer_bip_floors_qty()
    test_funding_debits_never_credits()
    test_liq_flattens()
    test_refuses_leverage_gt_4()
    print("ok ledger")
