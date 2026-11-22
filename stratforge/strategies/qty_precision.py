"""Conservative quantity rounding for pre-trade sizing.

Floor (never round up) so notional+fee cannot exceed the cash we sized from.
Live native path also applies instrument.make_qty; this is the executor-side
guard used before submit.

Sells must never exceed wallet available: Coinbase portfolio crypto fields
round up (XLM 1620.94349082 → 1620.9435) and a market IOC of the rounded
lot fails PREVIEW_INSUFFICIENT_FUND.
"""
from __future__ import annotations

import math
from decimal import Decimal


def floor_qty(qty: float, increment: float | None = None, decimals: int = 8) -> float:
    q = float(qty)
    if q <= 0:
        return 0.0
    if increment is not None and increment > 0:
        n = math.floor((q / increment) + 1e-12)
        return float(n * increment)
    scale = 10 ** int(decimals)
    return math.floor(q * scale + 1e-12) / scale


def clip_sell_qty(requested: float | str, available: float | str) -> float:
    """Return a sell size that never exceeds venue available (no round-up)."""
    try:
        req = Decimal(str(requested))
        av = Decimal(str(available))
    except (ArithmeticError, ValueError, TypeError):
        return 0.0
    if req <= 0 or av <= 0:
        return 0.0
    qty = req if req <= av else av
    as_float = float(qty)
    # float() can round up one ulp; if it would exceed available, step down.
    if Decimal(str(as_float)) > av:
        as_float = float(av)
        if Decimal(str(as_float)) > av:
            return 0.0
    return as_float
