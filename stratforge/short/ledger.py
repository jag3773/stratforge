"""
CFM-style 1x short ledger.

Futures semantics (not spot):
  equity = cash + unrealized
  opening a short posts initial margin and pays a fee; it does not credit sale proceeds
  short PnL = (entry - mark) * qty
  funding is charged to shorts on an 8h clock and is never credited

PAPER / RESEARCH ONLY. No exchange calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CONTRACTS = {
    "BTC": 0.01,  # nano BIP
    "ETH": 0.10,  # nano ETP
}


@dataclass
class Lot:
    symbol: str
    side: str
    qty: float
    entry: float
    ts: int
    margin: float
    fee_open: float


@dataclass
class ShortLedger:
    start_usd: float
    max_leverage: float = 1.0
    fee_rate: float = 0.0004
    funding_per_8h: float = 0.00003
    maintenance_margin_pct: float = 0.50
    integer_contracts: bool = False
    cash: float = 0.0
    lots: dict[str, Lot] = field(default_factory=dict)
    last_price: dict[str, float] = field(default_factory=dict)
    last_ts: int = 0
    last_funding_ts: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    liquidations: int = 0
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    closed: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.start_usd)

    # ----- marks / equity -----

    def update_price(self, symbol: str, price: float, ts: int = 0) -> None:
        self.last_price[symbol.upper()] = float(price)
        if ts:
            self.last_ts = int(ts)

    def unrealized(self, symbol: str | None = None) -> float:
        total = 0.0
        items = [(symbol.upper(), self.lots[symbol.upper()])] if symbol else list(self.lots.items())
        for sym, lot in items:
            if lot is None:
                continue
            mark = float(self.last_price.get(sym, lot.entry))
            if lot.side == "SHORT":
                total += (lot.entry - mark) * lot.qty
            else:
                total += (mark - lot.entry) * lot.qty
        return float(total)

    def margin_used(self) -> float:
        return float(sum(l.margin for l in self.lots.values()))

    def equity(self) -> float:
        return float(self.cash + self.unrealized())

    def available(self) -> float:
        return float(self.equity() - self.margin_used())

    def positions(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sym, lot in self.lots.items():
            mark = float(self.last_price.get(sym, lot.entry))
            upnl = (lot.entry - mark) * lot.qty if lot.side == "SHORT" else (mark - lot.entry) * lot.qty
            out[sym] = {
                "side": lot.side,
                "qty": lot.qty,
                "entry": lot.entry,
                "ts": lot.ts,
                "margin": lot.margin,
                "mark": mark,
                "upnl": upnl,
            }
        return out

    # ----- funding / liq -----

    def apply_funding(self, ts: int) -> None:
        """Charge shorts every 8h of wall time. Never credit."""
        if not self.lots:
            self.last_funding_ts = int(ts)
            return
        if self.last_funding_ts <= 0:
            self.last_funding_ts = int(ts)
            return
        elapsed = int(ts) - self.last_funding_ts
        step = 8 * 3600 * 1_000_000_000
        if elapsed < step:
            return
        n_steps = elapsed // step
        rate = float(self.funding_per_8h) * n_steps
        charged = 0.0
        for sym, lot in self.lots.items():
            mark = float(self.last_price.get(sym, lot.entry))
            notional = abs(lot.qty * mark)
            debit = notional * rate
            self.cash -= debit
            charged += debit
        self.funding_paid += charged
        self.last_funding_ts += n_steps * step
        if charged > 0:
            self.events.append(
                {"type": "FUNDING", "ts": int(ts), "debit": charged, "steps": int(n_steps)}
            )

    def maybe_liquidate(self, ts: int, price_hint: float | None = None) -> list[dict]:
        eq = self.equity()
        maint = self.margin_used() * float(self.maintenance_margin_pct)
        if eq > maint and eq > 0:
            return []
        actions: list[dict] = []
        for sym in list(self.lots.keys()):
            px = float(self.last_price.get(sym, price_hint or 0.0))
            if px <= 0:
                continue
            rec = self.cover(sym, px, ts, reason="liquidation")
            if rec:
                rec["liquidation"] = True
                actions.append(rec)
        if actions:
            self.liquidations += 1
        return actions

    # ----- sizing / orders -----

    def size_qty(self, symbol: str, price: float, target_notional: float) -> float:
        px = float(price)
        if px <= 0 or target_notional <= 0:
            return 0.0
        avail = self.available()
        # 1x: margin == notional; cannot use more than available after fee
        fee_pad = 1.0 + float(self.fee_rate)
        max_notional = max(0.0, avail / fee_pad)
        notional = min(float(target_notional), max_notional)
        if self.max_leverage > 0:
            # margin = notional / leverage
            max_by_lev = avail * float(self.max_leverage) / fee_pad
            notional = min(notional, max_by_lev)
        qty = notional / px
        if self.integer_contracts:
            mult = CONTRACTS.get(symbol.upper())
            if not mult:
                return 0.0
            n = int(qty / mult)
            qty = n * mult
        if qty * px < 1.0:
            return 0.0
        return float(qty)

    def short(self, symbol: str, price: float, ts: int, target_notional: float, reason: str) -> dict | None:
        sym = symbol.upper()
        if sym in self.lots:
            return None
        px = float(price)
        qty = self.size_qty(sym, px, target_notional)
        if qty <= 0:
            return None
        notional = qty * px
        fee = notional * float(self.fee_rate)
        margin = notional / max(float(self.max_leverage), 1e-9)
        if fee + 1e-9 > self.cash:
            return None
        if margin > self.available() + 1e-6:
            return None
        self.cash -= fee
        self.fees_paid += fee
        self.lots[sym] = Lot(
            symbol=sym,
            side="SHORT",
            qty=qty,
            entry=px,
            ts=int(ts),
            margin=margin,
            fee_open=fee,
        )
        self.last_price[sym] = px
        rec = {
            "type": "SHORT",
            "symbol": sym,
            "price": px,
            "qty": qty,
            "notional": notional,
            "fee": fee,
            "margin": margin,
            "reason": reason,
            "ts": int(ts),
            "cash": self.cash,
            "equity": self.equity(),
        }
        self.events.append(rec)
        return rec

    def cover(self, symbol: str, price: float, ts: int, reason: str) -> dict | None:
        sym = symbol.upper()
        lot = self.lots.get(sym)
        if lot is None:
            return None
        px = float(price)
        notional = lot.qty * px
        fee = notional * float(self.fee_rate)
        pnl = (lot.entry - px) * lot.qty if lot.side == "SHORT" else (px - lot.entry) * lot.qty
        self.cash += pnl - fee
        self.fees_paid += fee
        del self.lots[sym]
        self.last_price[sym] = px
        self.trades += 1
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        rec = {
            "type": "COVER",
            "symbol": sym,
            "price": px,
            "qty": lot.qty,
            "entry": lot.entry,
            "pnl": pnl,
            "fee": fee,
            "fee_open": lot.fee_open,
            "reason": reason,
            "ts": int(ts),
            "cash": self.cash,
            "equity": self.equity(),
        }
        self.closed.append(rec)
        self.events.append(rec)
        return rec

    def snapshot(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "equity": self.equity(),
            "unrealized": self.unrealized(),
            "margin_used": self.margin_used(),
            "available": self.available(),
            "n_open": len(self.lots),
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "liquidations": self.liquidations,
            "fees_paid": self.fees_paid,
            "funding_paid": self.funding_paid,
        }
