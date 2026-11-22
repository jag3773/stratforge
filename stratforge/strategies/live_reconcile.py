"""Compare local LIVE book to Coinbase (exchange is SSOT).

Never places orders. An empty / failed fetch is a MISMATCH (fail closed) —
we do not adopt an empty book over a non-empty local journal.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


QTY_TOL = 1e-6
CASH_TOL_USD = 2.0  # fees / dust; tighter than a sleeve position
_CASH_CCY = {"USD", "USDC", "USDT"}


@dataclass
class ReconcileResult:
    ok: bool
    reason: str
    mismatches: list[str] = field(default_factory=list)
    local_positions: dict[str, float] = field(default_factory=dict)
    exchange_positions: dict[str, float] = field(default_factory=dict)
    local_cash: float | None = None
    exchange_cash: float | None = None
    portfolio_id: str | None = None
    fetch_ok: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "mismatches": list(self.mismatches),
            "local_positions": dict(self.local_positions),
            "exchange_positions": dict(self.exchange_positions),
            "local_cash": self.local_cash,
            "exchange_cash": self.exchange_cash,
            "portfolio_id": self.portfolio_id,
            "fetch_ok": self.fetch_ok,
        }


def _base(sym: str) -> str:
    s = str(sym).replace("/", "-").upper().strip()
    if s.endswith("-USD"):
        s = s[:-4]
    if s.endswith("-USDC"):
        s = s[:-5]
    return s


def local_qty_map(positions: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym, p in (positions or {}).items():
        base = _base(sym)
        try:
            qty = float((p or {}).get("qty") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > QTY_TOL:
            out[base] = out.get(base, 0.0) + qty
    return out


def compare_books(
    *,
    local_positions: dict,
    local_cash: float | None,
    exchange_positions: dict[str, float],
    exchange_cash: float | None,
    portfolio_id: str | None = None,
    fetch_ok: bool = True,
    fetch_error: str = "",
    qty_tol: float = QTY_TOL,
    cash_tol: float = CASH_TOL_USD,
) -> ReconcileResult:
    local = local_qty_map(local_positions)
    exch = {_base(k): float(v) for k, v in (exchange_positions or {}).items() if float(v) > qty_tol}

    if not fetch_ok:
        return ReconcileResult(
            ok=False,
            reason=f"exchange fetch failed (fail closed): {fetch_error or 'unknown'}",
            mismatches=[f"fetch_failed:{fetch_error or 'unknown'}"],
            local_positions=local,
            exchange_positions=exch,
            local_cash=local_cash,
            exchange_cash=exchange_cash,
            portfolio_id=portfolio_id,
            fetch_ok=False,
        )

    mismatches: list[str] = []
    names = set(local) | set(exch)
    for name in sorted(names):
        lq = local.get(name, 0.0)
        eq = exch.get(name, 0.0)
        if abs(lq - eq) > qty_tol:
            mismatches.append(f"{name}: local={lq:.8g} exchange={eq:.8g}")

    if local_cash is not None and exchange_cash is not None:
        if abs(float(local_cash) - float(exchange_cash)) > cash_tol:
            mismatches.append(
                f"USD: local={float(local_cash):.2f} exchange={float(exchange_cash):.2f}"
            )

    ok = not mismatches
    return ReconcileResult(
        ok=ok,
        reason="match" if ok else "position/cash mismatch",
        mismatches=mismatches,
        local_positions=local,
        exchange_positions=exch,
        local_cash=local_cash,
        exchange_cash=exchange_cash,
        portfolio_id=portfolio_id,
        fetch_ok=True,
    )


def venue_cash_surplus(
    res: ReconcileResult, cash_tol: float = CASH_TOL_USD
) -> float:
    """USD the venue has above the journal when lots already match.

    Position mismatch, failed fetch, or a cash *decrease* return 0 — those
    stay fail-closed unless the caller book-syncs lots. Callers journal a
    CASH_DEPOSIT for a positive surplus.
    """
    delta = venue_cash_delta(res, cash_tol)
    return delta if delta > 0 else 0.0


def venue_cash_delta(
    res: ReconcileResult, cash_tol: float = CASH_TOL_USD
) -> float:
    """Signed venue-minus-local cash when lots match and fetch is healthy."""
    if not res.fetch_ok:
        return 0.0
    if any(not str(m).startswith("USD:") for m in (res.mismatches or [])):
        return 0.0
    if res.local_cash is None or res.exchange_cash is None:
        return 0.0
    delta = float(res.exchange_cash) - float(res.local_cash)
    return delta if abs(delta) > float(cash_tol) else 0.0


def required_portfolio_id() -> str:
    return (os.getenv("COINBASE_PORTFOLIO_ID") or "").strip()


def parse_portfolio_detail(detail: Any) -> dict[str, Any]:
    """Parse Coinbase portfolio-detail JSON into cash / spots.

    Advanced Trade returns ``{"breakdown": {spot_positions, portfolio_balances}}``
    (dict, not a list). Older list-shaped payloads are still accepted.
    """
    positions: dict[str, float] = {}
    cash = 0.0
    total = 0.0
    for row in _extract_breakdown(detail):
        ccy = _base(row.get("currency") or row.get("asset") or "")
        if not ccy:
            continue
        qty = _f(
            row.get("available_to_trade_crypto")
            or row.get("total_balance_crypto")
            or row.get("available_balance")
            or row.get("available")
            or row.get("quantity")
        )
        val = _f(
            row.get("total_balance_fiat")
            or row.get("available_to_trade_fiat")
            or row.get("value")
            or row.get("value_at_market")
        )
        is_cash = bool(row.get("is_cash")) or ccy in _CASH_CCY
        if val:
            total += val
        if is_cash:
            cash += val if val else qty
        elif qty > QTY_TOL:
            positions[ccy] = positions.get(ccy, 0.0) + qty
    pb_total = _portfolio_balances_total(detail)
    if pb_total > 0 and total <= 0.01:
        total = pb_total
    if pb_total > 0 and cash <= 0.01 and not positions:
        cash = pb_total
    return {
        "positions": positions,
        "cash_usd": cash,
        "portfolio_total_usd": total,
    }


def overlay_account_available(
    positions: dict[str, float],
    accounts: list[dict] | None,
    portfolio_id: str | None = None,
) -> dict[str, float]:
    """Replace portfolio-rounded lots with wallet available_balance.

    Coinbase portfolio ``available_to_trade_crypto`` rounds up (XLM fill
    1620.94349082 reported as 1620.9435). The accounts endpoint keeps the
    full string. Sells must use the wallet figure or Coinbase rejects with
    PREVIEW_INSUFFICIENT_FUND. Foreign ``retail_portfolio_id`` rows are ignored.
    """
    out = dict(positions or {})
    want = (portfolio_id or "").strip()
    for row in accounts or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("retail_portfolio_id") or "").strip()
        if want and rid and rid != want:
            continue
        ccy = _base(row.get("currency") or row.get("asset") or "")
        if not ccy or ccy in _CASH_CCY:
            continue
        qty = _f(row.get("available_balance"))
        if qty > QTY_TOL:
            out[ccy] = qty
    return out


def fetch_coinbase_book(portfolio_id: str | None = None) -> dict[str, Any]:
    """Read-only Coinbase Advanced Trade book for one portfolio.

    Returns a dict suitable for compare_books / tests. Never creates orders.
    """
    pid = (portfolio_id or required_portfolio_id()).strip()
    if not pid:
        return {
            "ok": False,
            "error": "COINBASE_PORTFOLIO_ID unset",
            "portfolio_id": None,
            "positions": {},
            "cash_usd": None,
            "portfolio_total_usd": None,
        }

    api_key = os.getenv("COINBASE_API_KEY") or ""
    from ccxt_bridge import normalize_pem_secret

    api_secret = normalize_pem_secret(os.getenv("COINBASE_API_SECRET") or "")
    if not (api_key and api_secret):
        return {
            "ok": False,
            "error": "missing COINBASE_API_KEY/SECRET",
            "portfolio_id": pid,
            "positions": {},
            "cash_usd": None,
            "portfolio_total_usd": None,
        }

    try:
        import ccxt
    except Exception as e:
        return {
            "ok": False,
            "error": f"ccxt import: {e}",
            "portfolio_id": pid,
            "positions": {},
            "cash_usd": None,
            "portfolio_total_usd": None,
        }

    client = ccxt.coinbase(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"fetchCurrencies": False, "portfolio": pid},
        }
    )
    try:
        detail = client.v3PrivateGetBrokeragePortfoliosPortfolioUuid({"portfolio_uuid": pid})
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "portfolio_id": pid,
            "positions": {},
            "cash_usd": None,
            "portfolio_total_usd": None,
        }

    parsed = parse_portfolio_detail(detail)
    positions = parsed["positions"]
    try:
        raw_accts = client.v3PrivateGetBrokerageAccounts({"limit": 250})
        accounts = (raw_accts or {}).get("accounts") if isinstance(raw_accts, dict) else None
        if isinstance(accounts, list):
            positions = overlay_account_available(positions, accounts, portfolio_id=pid)
    except Exception:
        pass
    return {
        "ok": True,
        "error": "",
        "portfolio_id": pid,
        "positions": positions,
        "cash_usd": parsed["cash_usd"],
        "portfolio_total_usd": parsed["portfolio_total_usd"],
        "raw_keys": list(detail.keys()) if isinstance(detail, dict) else [],
    }


def _extract_breakdown(detail: Any) -> list[dict]:
    if not isinstance(detail, dict):
        return []
    candidates: list[Any] = [detail]
    inner = detail.get("breakdown")
    if isinstance(inner, dict):
        candidates.append(inner)
    elif isinstance(inner, list):
        return [x for x in inner if isinstance(x, dict)]
    for blob in candidates:
        if not isinstance(blob, dict):
            continue
        for key in ("spot_positions", "positions", "balances"):
            v = blob.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    nested = detail.get("portfolio") or detail.get("data") or {}
    if isinstance(nested, dict):
        return _extract_breakdown(nested)
    return []


def _portfolio_balances_total(detail: Any) -> float:
    if not isinstance(detail, dict):
        return 0.0
    blobs = [detail, detail.get("breakdown")]
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        pb = blob.get("portfolio_balances")
        if not isinstance(pb, dict):
            continue
        total = _f(pb.get("total_balance"))
        if total > 0:
            return total
    return 0.0


def _f(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, dict):
        for k in ("value", "amount", "quantity"):
            if k in v:
                return _f(v[k])
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
