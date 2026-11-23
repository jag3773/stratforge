"""
PaperExecutor — SIMULATED brokerage for the live paper node.

* Virtual USD account (configurable start).
* Simulated MARKET fills at bar close.
* Long-only spot: BUY opens, CLOSE exits. No shorts, no leverage.
* Position notional capped at max_alloc_pct of equity.
* Every action journaled to data/execution_log.jsonl.
* Restart-safe: restore_from_journal() rebuilds cash/positions from the ledger.
* Thread-safe via RLock (equity/snapshot nested calls must not deadlock).

PAPER ONLY. No network, no exchange, no real funds.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL_PATH = os.path.join(HERE, "..", "data", "execution_log.jsonl")

from strategies.qty_precision import floor_qty


def _min_notional_only(reasons: list[str]) -> bool:
    """True when every deny is the $ min-notional check. Other denies stay visible."""
    if not reasons:
        return False
    return all("below min" in r for r in reasons)


def _ts_to_ns(ts: Any) -> int:
    """Parse journal ts (ISO str or numeric) to Unix ns. 0 if unknown."""
    if ts is None:
        return 0
    if isinstance(ts, (int, float)):
        v = int(ts)
        # Heuristic: ms vs ns vs sec
        if v > 10**17:  # ns
            return v
        if v > 10**14:  # µs
            return v * 1_000
        if v > 10**11:  # ms
            return v * 1_000_000
        return v * 1_000_000_000  # sec
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return 0
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1_000_000_000)
        except Exception:
            return 0
    return 0


class PaperExecutor:
    def __init__(
        self,
        start_usd: float = 1000.0,
        max_alloc_pct: float = 0.25,
        journal_path: str = JOURNAL_PATH,
        fee_rate: float = 0.001,
        write_opening: bool = False,
        limits=None,
    ):
        self.start_usd = float(start_usd)
        self.cash = float(start_usd)
        self.max_alloc_pct = float(max_alloc_pct)
        self.fee_rate = float(fee_rate)
        self.positions: dict[str, dict] = {}  # symbol -> {side, entry, qty, ts, fee}
        self.last_price: dict[str, float] = {}
        self.equity_curve: list[tuple[int, float]] = []
        self.trades = 0
        self.wins = 0
        self.losses = 0
        # RLock: _buy/_close/snapshot call equity while already holding the lock.
        self._lock = threading.RLock()
        self._journal_path = journal_path
        self.limits = limits
        self._halt_flatten_done = False
        from strategies.day_risk import DayRiskTracker

        self.day = DayRiskTracker()
        self._daily_halt_logged_day = None
        os.makedirs(os.path.dirname(os.path.abspath(self._journal_path)) or ".", exist_ok=True)
        if write_opening:
            self._journal(
                {
                    "action": "OPENING_BALANCE",
                    "capital": self.start_usd,
                    "start_date": datetime.now(timezone.utc).date().isoformat(),
                }
            )

    def restore_from_journal(self) -> dict:
        """
        Rebuild cash + open positions from the journal without appending.

        Prefers post-trade `cash` fields when present so restarts match the ledger.
        Does NOT reset or rewrite the journal. Safe to call once at node startup.
        """
        path = self._journal_path
        if not path or not os.path.isfile(path):
            return {"restored": False, "reason": "no_journal", "path": path}

        cash = float(self.start_usd)
        start_usd = float(self.start_usd)
        positions: dict[str, dict] = {}
        trades = 0
        wins = 0
        losses = 0
        n_lines = 0
        opening_seen = False
        last_action = None

        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n_lines += 1
                action = e.get("action")
                last_action = action

                if action == "OPENING_BALANCE":
                    start_usd = float(e.get("capital", start_usd))
                    cash = start_usd
                    positions = {}
                    trades = wins = losses = 0
                    opening_seen = True
                    continue

                if action == "CASH_DEPOSIT":
                    amt = float(e.get("amount") or e.get("capital") or 0.0)
                    if amt == 0.0:
                        continue
                    if "cash" in e and e["cash"] is not None:
                        cash = float(e["cash"])
                    else:
                        cash = float(cash) + amt
                    # Contributed capital rises with deposits (PnL baseline).
                    start_usd = float(start_usd) + amt
                    continue

                if action == "PAPER_BUY":
                    sym = str(e.get("symbol", "")).upper()
                    if not sym:
                        continue
                    price = float(e.get("price") or 0.0)
                    qty = float(e.get("qty") or 0.0)
                    fee = float(e.get("fee") or 0.0)
                    notional = float(e.get("notional") if e.get("notional") is not None else price * qty)
                    if "cash" in e and e["cash"] is not None:
                        cash = float(e["cash"])
                    else:
                        cash -= notional + fee
                    positions[sym] = {
                        "side": "LONG",
                        "entry": price,
                        "qty": qty,
                        "ts": _ts_to_ns(e.get("ts")),
                        "fee": fee,
                    }
                    trades += 1
                    continue

                if action == "PAPER_CLOSE":
                    sym = str(e.get("symbol", "")).upper()
                    if "cash" in e and e["cash"] is not None:
                        cash = float(e["cash"])
                    else:
                        price = float(e.get("price") or 0.0)
                        qty = float(e.get("qty") or 0.0)
                        fee = float(e.get("fee") or 0.0)
                        cash += qty * price - fee
                    if sym:
                        positions.pop(sym, None)
                    pnl = e.get("pnl")
                    if pnl is not None:
                        if float(pnl) >= 0:
                            wins += 1
                        else:
                            losses += 1
                    continue

                # CONFIG_UPDATE and other meta: ignore for book state

        with self._lock:
            self.start_usd = start_usd
            self.cash = cash
            self.positions = positions
            self.trades = trades
            self.wins = wins
            self.losses = losses

        return {
            "restored": True,
            "opening_seen": opening_seen,
            "lines": n_lines,
            "last_action": last_action,
            "cash": round(cash, 6),
            "n_positions": len(positions),
            "positions": sorted(positions.keys()),
            "trades": trades,
            "start_usd": start_usd,
        }

    def _journal(self, entry: dict):
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        entry["mode"] = "PAPER"
        with open(self._journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def deposit_cash(self, amount: float, reason: str = "manual_deposit") -> dict:
        """
        Add cash to the paper book and journal a CASH_DEPOSIT line.
        Restart-safe via restore_from_journal(). Does not place trades.
        """
        amt = float(amount)
        if amt <= 0:
            raise ValueError("deposit amount must be positive")
        with self._lock:
            self.cash = float(self.cash) + amt
            self.start_usd = float(self.start_usd) + amt
            eq = self._equity_unlocked()
            entry = {
                "action": "CASH_DEPOSIT",
                "amount": amt,
                "reason": reason,
                "cash": self.cash,
                "equity": eq,
                "start_usd": self.start_usd,
            }
            self._journal(entry)
            return {
                "amount": amt,
                "cash": round(self.cash, 6),
                "equity": round(eq, 6),
                "start_usd": round(self.start_usd, 6),
                "reason": reason,
            }

    def journal_meta(self, entry: dict):
        """Append a non-trade audit line (e.g. CONFIG_UPDATE)."""
        with self._lock:
            self._journal(entry)

    def update_price(self, symbol: str, price: float):
        with self._lock:
            self.last_price[symbol] = float(price)

    def _equity_unlocked(self) -> float:
        eq = self.cash
        for sym, p in self.positions.items():
            px = self.last_price.get(sym, p["entry"])
            if p["side"] == "LONG":
                eq += p["qty"] * px
        return eq

    def equity(self) -> float:
        with self._lock:
            return self._equity_unlocked()

    def _open_longs(self) -> int:
        return sum(1 for p in self.positions.values() if p["side"] == "LONG")

    def apply(self, actions: list[dict]):
        if self.limits is not None:
            from risk_limits import is_trading_halted

            with self._lock:
                self._roll_day_unlocked()
                halted, why = is_trading_halted(self.limits)
                if halted:
                    self._flatten_halt_unlocked(why)
                    return
        for a in actions:
            sym = a["symbol"]
            price = float(a.get("price") or self.last_price.get(sym, 0.0))
            if price <= 0:
                continue
            if a["type"] == "BUY":
                self._buy(sym, price, a.get("reason", ""))
            elif a["type"] == "CLOSE":
                self._close(sym, price, a.get("reason", ""))

    def _roll_day_unlocked(self) -> None:
        if self.day.roll(self._equity_unlocked()):
            self._journal(
                {
                    "action": "DAY_OPEN",
                    "day": self.day.day_utc.isoformat() if self.day.day_utc else None,
                    "equity": self._equity_unlocked(),
                }
            )
            self._daily_halt_logged_day = None

    def _flatten_halt_unlocked(self, why: str) -> None:
        if self._halt_flatten_done and not self.positions:
            return
        self._journal({"action": "HALT_ENGAGED", "reason": why, "n_positions": len(self.positions)})
        for sym in list(self.positions):
            px = float(self.last_price.get(sym) or self.positions[sym]["entry"])
            self._close(sym, px, reason=f"halt_flatten:{why}")
        self._halt_flatten_done = True

    def _buy(self, symbol: str, price: float, reason: str):
        with self._lock:
            if symbol in self.positions:
                return
            eq = self._equity_unlocked()
            # Cap by alloc and by affordable cash (notional + fee).
            # Size from total spend first so fee+notional never exceeds cash by float dust
            # (cash/(1+fee)*(1+fee) can be 1 ulp above cash and previously skipped all
            # residual-cash / partial-alloc buys after the book was ~fully allocated).
            target = eq * self.max_alloc_pct
            if self.limits is not None:
                cap = float(getattr(self.limits, "max_position_notional_usd", 0.0) or 0.0)
                total_cap = float(getattr(self.limits, "max_total_notional_usd", 0.0) or 0.0)
                min_n = float(getattr(self.limits, "min_notional_usd", 0.0) or 0.0)
                # A dollar cap under the minimum cannot fund a legal order. That is
                # a real deny, not residual cash. Residual cash (caps unused) stays silent.
                if min_n > 0 and ((0 < cap < min_n) or (0 < total_cap < min_n)):
                    self._journal(
                        {
                            "action": "RISK_DENY",
                            "symbol": symbol,
                            "side": "buy",
                            "notional": target,
                            "reasons": [f"dollar cap cannot fund min ${min_n:.2f}"],
                            "reason_signal": reason,
                        }
                    )
                    return
                if cap > 0:
                    target = min(target, cap)
            if target <= 0 or price <= 0 or self.cash <= 0:
                return
            max_spend = min(target * (1.0 + self.fee_rate), float(self.cash))
            if max_spend <= 0:
                return
            notional = max_spend / (1.0 + self.fee_rate)
            qty = floor_qty(notional / price)
            if qty <= 0:
                return
            notional = qty * price
            fee = notional * self.fee_rate
            cost = notional + fee
            # Final clamp — spend must never exceed cash (floor may still overshoot ulp).
            if cost > self.cash + 1e-12:
                notional = float(self.cash) / (1.0 + self.fee_rate)
                qty = floor_qty(notional / price)
                if qty <= 0:
                    return
                notional = qty * price
                fee = notional * self.fee_rate
                cost = notional + fee
            if notional <= 0 or cost <= 0 or cost > self.cash + 1e-12:
                return
            if self.limits is not None:
                from risk_limits import check_order

                if self.day.day_start_equity is None:
                    self._roll_day_unlocked()
                dloss = self.day.daily_loss_usd(eq)
                risk = check_order(
                    symbol=f"{symbol}-USD",
                    side="buy",
                    notional_usd=notional,
                    product="spot",
                    current_total_notional=sum(
                        abs(p["qty"] * self.last_price.get(s, p["entry"]))
                        for s, p in self.positions.items()
                    ),
                    daily_loss_usd=dloss,
                    open_positions=self._open_longs(),
                    equity=eq,
                    is_close=False,
                    limits=self.limits,
                    day_start_equity=self.day.day_start_equity,
                )
                if not risk.approved:
                    # A buy under min notional is residual cash, not a broken
                    # signal. Do not journal it. Real denies still land.
                    if not _min_notional_only(risk.reasons):
                        self._journal(
                            {
                                "action": "RISK_DENY",
                                "symbol": symbol,
                                "side": "buy",
                                "notional": notional,
                                "reasons": risk.reasons,
                                "reason_signal": reason,
                            }
                        )
                    return
            self.cash = float(self.cash) - cost
            if self.cash < 0 and abs(self.cash) < 1e-9:
                self.cash = 0.0
            self.positions[symbol] = {
                "side": "LONG",
                "entry": price,
                "qty": qty,
                "ts": int(time.time() * 1e9),
                "fee": fee,
            }
            self.trades += 1
            self._journal(
                {
                    "action": "PAPER_BUY",
                    "symbol": symbol,
                    "price": price,
                    "qty": qty,
                    "notional": notional,
                    "fee": fee,
                    "reason": reason,
                    "cash": self.cash,
                    "equity": self._equity_unlocked(),
                }
            )

    def _close(self, symbol: str, price: float, reason: str):
        with self._lock:
            p = self.positions.get(symbol)
            if not p:
                return
            qty = p["qty"]
            notional = qty * price
            fee = notional * self.fee_rate
            # Gross PnL vs entry; fee on exit only here (entry fee already left cash).
            pnl = (price - p["entry"]) * qty - fee - float(p.get("fee") or 0.0)
            self.cash += notional - fee
            if hasattr(self, "day"):
                self.day.add_realized(pnl)
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1
            del self.positions[symbol]
            self._journal(
                {
                    "action": "PAPER_CLOSE",
                    "symbol": symbol,
                    "price": price,
                    "qty": qty,
                    "notional": notional,
                    "pnl": pnl,
                    "fee": fee,
                    "reason": reason,
                    "cash": self.cash,
                    "equity": self._equity_unlocked(),
                }
            )

    def snapshot(self) -> dict:
        with self._lock:
            eq = self._equity_unlocked()
            return {
                "equity": round(eq, 2),
                "cash": round(self.cash, 2),
                "positions": {
                    sym: {
                        "side": p["side"],
                        "entry": p["entry"],
                        "qty": p["qty"],
                        "mark": self.last_price.get(sym, p["entry"]),
                        "ts": p.get("ts", 0),
                    }
                    for sym, p in self.positions.items()
                },
                "n_positions": len(self.positions),
                "open_longs": self._open_longs(),
                "trades": self.trades,
                "wins": self.wins,
                "losses": self.losses,
                "return_pct": round((eq / self.start_usd - 1) * 100, 2) if self.start_usd else 0.0,
            }
