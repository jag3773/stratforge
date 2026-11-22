"""CFM-style futures ledger (long and short).

Equity = cash + unrealized. Opening posts initial margin and a fee; it does
not spend the full notional. Funding is a constant debit (never a credit).
Overnight IM default 25% (4x). 10x advertised leverage is not used on 4h holds.

PAPER / RESEARCH ONLY. No exchange calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_MULT = {"BTC": 0.01, "ETH": 0.10, "SOL": 5.0, "XRP": 500.0}


@dataclass
class Lot:
    symbol: str
    side: str  # LONG | SHORT
    qty: float
    entry: float
    ts: int
    margin: float
    fee_open: float
    stop_px: float | None = None


@dataclass
class FuturesLedger:
    start_usd: float
    max_leverage: float = 4.0
    fee_rate: float = 0.0004
    funding_per_8h: float = 0.00003
    funding_interval_hours: float = 8.0
    maintenance_frac_of_im: float = 0.50
    integer_contracts: bool = True
    multipliers: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MULT))
    cash: float = 0.0
    lots: dict[str, Lot] = field(default_factory=dict)
    last_price: dict[str, float] = field(default_factory=dict)
    last_ts: int = 0
    last_funding_ts: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    liquidations: int = 0
    stop_exits: int = 0
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    closed: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = float(self.start_usd)
        if float(self.max_leverage) > 4.0 + 1e-9:
            raise ValueError("research max_leverage cannot exceed overnight 4x")

    def update_price(self, symbol: str, price: float, ts: int = 0) -> None:
        self.last_price[symbol.upper()] = float(price)
        if ts:
            self.last_ts = int(ts)

    def unrealized(self, symbol: str | None = None) -> float:
        total = 0.0
        items = [(symbol.upper(), self.lots[symbol.upper()])] if symbol else list(self.lots.items())
        for sym, lot in items:
            mark = float(self.last_price.get(sym, lot.entry))
            total += self._upnl(lot, mark)
        return float(total)

    @staticmethod
    def _upnl(lot: Lot, mark: float) -> float:
        if lot.side == "SHORT":
            return (lot.entry - mark) * lot.qty
        return (mark - lot.entry) * lot.qty

    def margin_used(self) -> float:
        return float(sum(l.margin for l in self.lots.values()))

    def equity(self) -> float:
        return float(self.cash + self.unrealized())

    def available(self) -> float:
        return float(self.equity() - self.margin_used())

    def im_pct(self) -> float:
        return 1.0 / max(float(self.max_leverage), 1e-9)

    def mm_pct_notional(self) -> float:
        return self.im_pct() * float(self.maintenance_frac_of_im)

    def floor_qty(self, symbol: str, qty: float) -> float:
        if not self.integer_contracts:
            return max(0.0, float(qty))
        mult = float(self.multipliers.get(symbol.upper()) or 0.0)
        if mult <= 0:
            return 0.0
        n = int(abs(qty) / mult)
        return float(n * mult)

    def apply_funding(self, ts: int) -> None:
        if self.last_funding_ts <= 0:
            self.last_funding_ts = int(ts)
            return
        elapsed = int(ts) - self.last_funding_ts
        hours = max(float(self.funding_interval_hours), 1e-9)
        step = int(hours * 3600 * 1_000_000_000)
        if elapsed < step:
            return
        n_steps = elapsed // step
        rate = float(self.funding_per_8h) * n_steps
        charged = 0.0
        for sym, lot in self.lots.items():
            mark = float(self.last_price.get(sym, lot.entry))
            charged += abs(lot.qty * mark) * rate
        if charged > 0:
            self.cash -= charged
            self.funding_paid += charged
            self.events.append({"type": "FUNDING", "ts": int(ts), "debit": charged, "steps": int(n_steps)})
        self.last_funding_ts += n_steps * step

    def _liq_price(self, lot: Lot) -> float | None:
        """Price where equity = MM * current notional, other lots marked at last."""
        mm = self.mm_pct_notional()
        others = 0.0
        for sym, l in self.lots.items():
            if l is lot:
                continue
            others += self._upnl(l, float(self.last_price.get(sym, l.entry)))
        # cash + others + upnl(p) = mm * qty * p
        # long: cash+others + (p-entry)*q = mm*q*p
        q = lot.qty
        if q <= 0:
            return None
        base = self.cash + others
        if lot.side == "LONG":
            # base - entry*q + p*q = mm*q*p  => base - entry*q = p*q*(mm-1)
            den = q * (mm - 1.0)
            if abs(den) < 1e-18:
                return None
            return (base - lot.entry * q) / den
        # short: base + (entry-p)*q = mm*q*p  => base + entry*q = p*q*(mm+1)
        den = q * (mm + 1.0)
        if abs(den) < 1e-18:
            return None
        return (base + lot.entry * q) / den

    def maybe_liquidate_bar(self, symbol: str, high: float, low: float, ts: int) -> dict | None:
        sym = symbol.upper()
        lot = self.lots.get(sym)
        if lot is None:
            return None
        lp = self._liq_price(lot)
        if lp is None or lp <= 0:
            return None
        hit = False
        px = None
        if lot.side == "LONG" and low <= lp:
            hit, px = True, max(float(low), float(lp))
        elif lot.side == "SHORT" and high >= lp:
            hit, px = True, min(float(high), float(lp))
        if not hit or px is None or px <= 0:
            return None
        self.update_price(sym, px, ts)
        rec = self.close(sym, px, ts, reason="liquidation")
        if rec:
            rec["liquidation"] = True
            self.liquidations += 1
        return rec

    def maybe_stop_bar(self, symbol: str, high: float, low: float, ts: int) -> dict | None:
        sym = symbol.upper()
        lot = self.lots.get(sym)
        if lot is None or lot.stop_px is None:
            return None
        sp = float(lot.stop_px)
        hit = False
        px = None
        if lot.side == "LONG" and low <= sp:
            hit, px = True, sp
        elif lot.side == "SHORT" and high >= sp:
            hit, px = True, sp
        if not hit or px is None:
            return None
        self.update_price(sym, px, ts)
        rec = self.close(sym, px, ts, reason="stop")
        if rec:
            rec["stop"] = True
            self.stop_exits += 1
        return rec

    def close(self, symbol: str, price: float, ts: int, reason: str) -> dict | None:
        sym = symbol.upper()
        lot = self.lots.pop(sym, None)
        if lot is None:
            return None
        px = float(price)
        pnl = self._upnl(lot, px)
        fee = abs(lot.qty * px) * float(self.fee_rate)
        self.cash += pnl - fee
        self.fees_paid += fee
        self.trades += 1
        if pnl - fee >= 0:
            self.wins += 1
        else:
            self.losses += 1
        rec = {
            "type": "CLOSE",
            "symbol": sym,
            "side": lot.side,
            "qty": lot.qty,
            "entry": lot.entry,
            "exit": px,
            "pnl": pnl - fee,
            "fee": fee,
            "reason": reason,
            "ts": int(ts),
        }
        self.closed.append(rec)
        self.events.append(rec)
        return rec

    def open_pos(
        self,
        symbol: str,
        side: str,
        price: float,
        ts: int,
        target_notional: float,
        reason: str,
        stop_px: float | None = None,
    ) -> dict | None:
        sym = symbol.upper()
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            return None
        if sym in self.lots:
            return None
        px = float(price)
        if px <= 0 or target_notional <= 0:
            return None
        eq = self.equity()
        if eq <= 0:
            return None
        fee_pad = 1.0 + float(self.fee_rate)
        max_notional = max(0.0, self.available() * float(self.max_leverage) / fee_pad)
        notional = min(float(target_notional), max_notional)
        qty = self.floor_qty(sym, notional / px)
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
            side=side,
            qty=qty,
            entry=px,
            ts=int(ts),
            margin=margin,
            fee_open=fee,
            stop_px=stop_px,
        )
        rec = {
            "type": "OPEN",
            "symbol": sym,
            "side": side,
            "qty": qty,
            "price": px,
            "margin": margin,
            "fee": fee,
            "reason": reason,
            "ts": int(ts),
        }
        self.events.append(rec)
        self.trades += 1
        return rec

    def flatten_all(self, prices: dict[str, float], ts: int, reason: str) -> None:
        for sym in list(self.lots.keys()):
            px = float(prices.get(sym, self.last_price.get(sym, 0.0)))
            if px > 0:
                self.close(sym, px, ts, reason=reason)

    def set_stop(self, symbol: str, stop_px: float | None) -> None:
        lot = self.lots.get(symbol.upper())
        if lot is not None:
            lot.stop_px = stop_px

    def rebalance(
        self,
        symbol: str,
        side: str,
        price: float,
        ts: int,
        target_notional: float,
        reason: str,
        stop_px: float | None = None,
    ) -> None:
        """Match target side/notional. Closes and reopens when side or size changes by >= 1 lot."""
        sym = symbol.upper()
        side = side.upper()
        px = float(price)
        self.update_price(sym, px, ts)
        if side == "FLAT" or target_notional <= 0:
            if sym in self.lots:
                self.close(sym, px, ts, reason=reason + "_flat")
            return
        lot = self.lots.get(sym)
        want_qty = self.floor_qty(sym, float(target_notional) / px)
        if want_qty <= 0:
            if lot:
                self.close(sym, px, ts, reason=reason + "_flat")
            return
        if lot and lot.side == side:
            mult = float(self.multipliers.get(sym) or 0.0) or 1.0
            if abs(lot.qty - want_qty) < 0.5 * mult:
                lot.stop_px = stop_px
                lot.margin = (lot.qty * px) / max(float(self.max_leverage), 1e-9)
                return
            self.close(sym, px, ts, reason=reason + "_resize")
        elif lot:
            self.close(sym, px, ts, reason=reason + "_flip")
        self.open_pos(sym, side, px, ts, want_qty * px, reason=reason, stop_px=stop_px)
