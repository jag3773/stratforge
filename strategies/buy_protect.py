"""Buy-protect gates: skip wide/chased books, else take at ask."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BuyProtectDecision:
    skip: bool
    reason: str | None  # None when taking; else "book" | "spread" | "chase"
    spread_bps: float | None
    limit_price: float | None  # ask when taking
    bid: float | None
    ask: float | None


def _book_skip(bid: float | None, ask: float | None) -> BuyProtectDecision:
    return BuyProtectDecision(
        skip=True,
        reason="book",
        spread_bps=None,
        limit_price=None,
        bid=bid,
        ask=ask,
    )


def decide_buy(
    bid: float | None,
    ask: float | None,
    close: float,
    *,
    max_spread_bps: float = 40.0,
    chase_cap_bps: float = 15.0,
) -> BuyProtectDecision:
    if bid is None or ask is None:
        return _book_skip(bid, ask)
    if (
        close is None
        or not math.isfinite(bid)
        or not math.isfinite(ask)
        or not math.isfinite(close)
        or bid <= 0
        or ask <= 0
        or close <= 0
        or ask < bid
    ):
        return _book_skip(bid, ask)

    mid = (bid + ask) / 2.0
    spread_bps = (ask - bid) / mid * 1e4
    if spread_bps > max_spread_bps:
        return BuyProtectDecision(
            skip=True,
            reason="spread",
            spread_bps=spread_bps,
            limit_price=None,
            bid=bid,
            ask=ask,
        )
    if ask > close * (1.0 + chase_cap_bps / 1e4):
        return BuyProtectDecision(
            skip=True,
            reason="chase",
            spread_bps=spread_bps,
            limit_price=None,
            bid=bid,
            ask=ask,
        )
    return BuyProtectDecision(
        skip=False,
        reason=None,
        spread_bps=spread_bps,
        limit_price=ask,
        bid=bid,
        ask=ask,
    )
