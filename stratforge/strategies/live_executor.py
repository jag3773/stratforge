"""
LiveExecutor — Coinbase spot execution behind risk gates.

Same apply/update_price/equity/positions/snapshot surface as PaperExecutor.

Modes of the *bridge*:
  dry_run=True  -> SHADOW/dry: journals DRY_RUN_ORDER, updates local position state
                  as if filled (for parity tracking). No exchange call.
  dry_run=False -> LIVE: real orders via the selected backend (still needs
                  LIVE_TRADING + SIMULATION_MODE=false + approval file).

Backends:
  ccxt — ccxt_bridge (Python REST). Native/v2/nautilus backends are rejected.

v1: long-only spot. BUY opens, CLOSE sells. No shorts.

P0 safety (2026-08-16):
  - refuse new BUYs until LIVE book reconciles with exchange
  - book only filled qty (partial close keeps remainder)
  - persist client_order_id before submit; query after timeout/error
  - UTC-day MTM loss halt
  - never submit if cash < estimated cost
  - halt file flattens (closes still allowed)
  - venue USD surplus with matching lots → CASH_DEPOSIT (no restart);
    cash drop / lot drift stay fail-closed
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from stratforge.state import state_root

ROOT = state_root()

from risk_limits import RiskLimits, check_order, limits_from_config, load_limits_from_env, is_trading_halted, raise_total_notional_for_contributed, clip_target_usd, effective_daily_loss_halt
import ccxt_bridge
from strategies.buy_protect import decide_buy
from strategies.day_risk import DayRiskTracker, utc_today
from strategies.live_reconcile import ReconcileResult, compare_books, venue_cash_delta
from strategies.qty_precision import clip_sell_qty, floor_qty

DEFAULT_JOURNAL = ROOT / "data" / "execution_log_live.jsonl"
QTY_EPS = 1e-12
VENUE_DOWN_COOLDOWN_SEC = 3600.0
_VENUE_DOWN_MARKERS = (
    "unable to load pem",
)


def _is_venue_down(err: object) -> bool:
    s = str(err or "").lower()
    return any(m in s for m in _VENUE_DOWN_MARKERS)


def _is_insufficient_fund(err: object) -> bool:
    """Coinbase preview/create reject when the quote hold does not fit.

    Buy-side this is a fee-margin miss (cash is present). Sell-side it is a
    base-size over-ask. Callers must not treat the two as the same retry.
    """
    return "INSUFFICIENT_FUND" in str(err or "").upper()


def qty_within_fee_pad(cash: float, price: float, fee_rate: float) -> float:
    """Floor a buy qty so qty * price * (1 + fee_rate) fits in cash."""
    if cash <= 0 or price <= 0 or fee_rate < 0:
        return 0.0
    notional = float(cash) / (1.0 + float(fee_rate))
    return floor_qty(notional / float(price))


def _load_backend(name: str | None):
    raw = (name or os.getenv("EXECUTION_BACKEND") or "ccxt").strip().lower()
    if raw in ("native_v2", "native", "v2", "nautilus"):
        raise ValueError(f"execution backend {raw!r} is not supported; ccxt only")
    return "ccxt", ccxt_bridge


def _parse_ts(ts) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _ts_ns(ts) -> int:
    """Journal stamp as nanoseconds. ISO strings and epoch seconds both occur."""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        n = int(ts)
        if n > 10**14:
            return n
        if n > 10**11:
            return n * 1_000_000
        return n * 1_000_000_000
    dt = _parse_ts(ts)
    if dt is None:
        return int(time.time() * 1e9)
    return int(dt.timestamp() * 1_000_000_000)


class LiveExecutor:
    def __init__(
        self,
        start_usd: float = 1000.0,
        max_alloc_pct: float = 0.25,
        journal_path: str | Path = DEFAULT_JOURNAL,
        fee_rate: float = 0.001,
        dry_run: bool | None = None,
        limits: RiskLimits | None = None,
        mode_label: str = "SHADOW",
        backend: str | None = None,
        bridge_module=None,
        write_start: bool = True,
        buy_protect: dict | None = None,
        venue_taker_fee_rate: float = 0.012,
    ):
        self.start_usd = float(start_usd)
        self.cash = float(start_usd)
        self.max_alloc_pct = float(max_alloc_pct)
        self.fee_rate = float(fee_rate)
        # Paper/research fee_rate is 10 bps. Coinbase taker hold on this sleeve
        # has been 90–120 bps. Cash-bound LIVE buys must reserve the venue rate
        # or the preview rejects PREVIEW_INSUFFICIENT_FUND with cash still there.
        self.venue_taker_fee_rate = float(venue_taker_fee_rate)
        self.positions: dict[str, dict] = {}
        self.last_price: dict[str, float] = {}
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.day = DayRiskTracker()
        self._lock = threading.RLock()
        self._journal_path = str(journal_path)
        self.mode_label = mode_label
        self.limits = limits or load_limits_from_env(
            RiskLimits(max_alloc_pct=max_alloc_pct)
        )
        os.makedirs(os.path.dirname(os.path.abspath(self._journal_path)) or ".", exist_ok=True)
        if bridge_module is not None:
            self.backend_name = "test"
            self._bridge = bridge_module
        else:
            self.backend_name, self._bridge = _load_backend(backend)

        if dry_run is None:
            self.dry_run = self._bridge.bridge_config()["dry_run"]
        else:
            self.dry_run = bool(dry_run)

        bcfg = self._bridge.bridge_config(self._journal_path)
        if not self.dry_run and bcfg["dry_run"]:
            self.dry_run = True
            self._bridge_blocked_reason = bcfg.get("approval_reason") or "bridge dry_run"
        else:
            self._bridge_blocked_reason = None

        # Shadow/dry has no exchange book to match. LIVE starts blocked until reconcile.
        self._reconciled = bool(self.dry_run)
        self._last_reconcile: ReconcileResult | None = None
        self._halt_flatten_done = False
        self._daily_halt_logged_day = None
        self._venue_down_until = 0.0
        self._venue_down_logged = False
        self._last_venue_sync_mono = 0.0
        self._venue_book_fetch = None
        if not self.dry_run and bridge_module is None:
            from strategies.live_reconcile import fetch_coinbase_book

            self._venue_book_fetch = fetch_coinbase_book

        bp = buy_protect or {}
        self._buy_protect_enabled = bool(bp.get("enabled", False))
        self._buy_protect_max_spread_bps = float(bp.get("max_spread_bps", 40))
        self._buy_protect_chase_cap_bps = float(bp.get("chase_cap_bps", 15))

        if write_start:
            self._journal(
                {
                    "action": "EXECUTOR_START",
                    "mode": self.mode_label,
                    "dry_run": self.dry_run,
                    "start_usd": self.start_usd,
                    "max_alloc_pct": self.max_alloc_pct,
                    "backend": self.backend_name,
                    "reconciled": self._reconciled,
                    "allowed_symbols": (
                        None
                        if not self.limits.allowed_symbols
                        else list(self.limits.allowed_symbols)
                    ),
                    "bridge_blocked": self._bridge_blocked_reason,
                }
            )

    @property
    def reconciled(self) -> bool:
        return bool(self._reconciled)

    @property
    def daily_realized_pnl(self) -> float:
        return float(self.day.daily_realized_pnl)

    def restore_from_journal(self) -> dict:
        """Rebuild cash + open lots from SHADOW_/LIVE_ BUY/CLOSE. Does not wipe the journal."""
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
        last_action = None
        buy_tags = {"SHADOW_BUY", "LIVE_BUY"}
        close_tags = {"SHADOW_CLOSE", "LIVE_CLOSE"}
        partial_tags = {"SHADOW_CLOSE_PARTIAL", "LIVE_CLOSE_PARTIAL"}
        day = DayRiskTracker()
        today = utc_today()

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
                evt_day = None
                ts = _parse_ts(e.get("ts"))
                if ts is not None:
                    evt_day = ts.astimezone(timezone.utc).date()

                if action == "OPENING_BALANCE":
                    start_usd = float(e.get("capital", start_usd))
                    cash = start_usd
                    positions = {}
                    trades = wins = losses = 0
                    continue

                if action == "CASH_DEPOSIT":
                    amt = float(e.get("amount") or e.get("capital") or 0.0)
                    if amt == 0.0:
                        continue
                    if e.get("cash") is not None:
                        cash = float(e["cash"])
                    else:
                        cash = float(cash) + amt
                    start_usd = float(start_usd) + amt
                    continue

                if action == "CASH_WITHDRAW":
                    amt = float(e.get("amount") or e.get("capital") or 0.0)
                    if amt == 0.0:
                        continue
                    if e.get("cash") is not None:
                        cash = float(e["cash"])
                    else:
                        cash = max(0.0, float(cash) - amt)
                    if e.get("start_usd") is not None:
                        start_usd = float(e["start_usd"])
                    else:
                        start_usd = max(0.0, float(start_usd) - amt)
                    continue

                if action == "BOOK_SYNC":
                    if e.get("cash") is not None:
                        cash = float(e["cash"])
                    raw_pos = e.get("positions") or {}
                    new_pos: dict = {}
                    for raw_sym, spec in raw_pos.items():
                        sym = str(raw_sym).upper()
                        if isinstance(spec, dict):
                            qty = float(spec.get("qty") or 0.0)
                            entry_px = float(spec.get("entry") or 0.0)
                            fee = float(spec.get("fee") or 0.0)
                        else:
                            qty = float(spec or 0.0)
                            prev = positions.get(sym) or {}
                            entry_px = float(prev.get("entry") or 0.0)
                            fee = float(prev.get("fee") or 0.0)
                        if qty > QTY_EPS:
                            new_pos[sym] = {
                                "side": "LONG",
                                "entry": entry_px,
                                "qty": qty,
                                "ts": _ts_ns(e.get("ts")),
                                "fee": fee,
                            }
                    positions = new_pos
                    continue

                if action == "DAY_OPEN":
                    try:
                        from datetime import date as _date

                        d = _date.fromisoformat(str(e.get("day")))
                    except Exception:
                        d = evt_day
                    if d is not None:
                        day.day_utc = d
                        if e.get("equity") is not None:
                            day.day_start_equity = float(e["equity"])
                        if d != today:
                            day.daily_realized_pnl = 0.0
                    continue

                if action in buy_tags:
                    sym = str(e.get("symbol", "")).upper()
                    if not sym:
                        continue
                    price = float(e.get("price") or 0.0)
                    qty = float(e.get("qty") or 0.0)
                    fee = float(e.get("fee") or 0.0)
                    if e.get("cash") is not None:
                        cash = float(e["cash"])
                    else:
                        cash -= qty * price + fee
                    if qty > QTY_EPS:
                        positions[sym] = {
                            "side": "LONG",
                            "entry": price,
                            "qty": qty,
                            "ts": _ts_ns(e.get("ts")),
                            "fee": fee,
                        }
                    trades += 1
                elif action in partial_tags:
                    sym = str(e.get("symbol", "")).upper()
                    if e.get("cash") is not None:
                        cash = float(e["cash"])
                    else:
                        price = float(e.get("price") or 0.0)
                        qty = float(e.get("qty") or 0.0)
                        fee = float(e.get("fee") or 0.0)
                        cash += qty * price - fee
                    rem = e.get("remaining_qty")
                    if sym and rem is not None and float(rem) > QTY_EPS:
                        prev = positions.get(sym) or {}
                        positions[sym] = {
                            "side": "LONG",
                            "entry": float(prev.get("entry") or e.get("entry") or 0.0),
                            "qty": float(rem),
                            "ts": _ts_ns(e.get("ts") or prev.get("ts")),
                            "fee": float(prev.get("fee") or e.get("fee") or 0.0),
                        }
                    elif sym:
                        positions.pop(sym, None)
                    pnl = e.get("pnl")
                    if pnl is not None:
                        if evt_day == today:
                            day.add_realized(float(pnl))
                        if float(pnl) >= 0:
                            wins += 1
                        else:
                            losses += 1
                elif action in close_tags:
                    sym = str(e.get("symbol", "")).upper()
                    if e.get("cash") is not None:
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
                        if evt_day == today:
                            day.add_realized(float(pnl))
                        if float(pnl) >= 0:
                            wins += 1
                        else:
                            losses += 1

        with self._lock:
            self.start_usd = start_usd
            self.cash = cash
            self.positions = positions
            self.trades = trades
            self.wins = wins
            self.losses = losses
            self.day = day
            cap = raise_total_notional_for_contributed(self.limits, start_usd)

        return {
            "restored": True,
            "lines": n_lines,
            "last_action": last_action,
            "cash": round(cash, 6),
            "n_positions": len(positions),
            "positions": sorted(positions.keys()),
            "trades": trades,
            "start_usd": start_usd,
            "max_total_notional_usd": cap,
            "day": self.day.snapshot(),
        }

    def apply_reconcile(
        self,
        exchange_book: dict | None = None,
        *,
        fetch_ok: bool = True,
        fetch_error: str = "",
    ) -> ReconcileResult:
        """Compare local book to an exchange snapshot. Fail closed on fetch error."""
        with self._lock:
            if self.dry_run:
                self._reconciled = True
                res = ReconcileResult(
                    ok=True,
                    reason="dry_run — no exchange book",
                    local_positions={k: float(v["qty"]) for k, v in self.positions.items()},
                    fetch_ok=True,
                )
                self._last_reconcile = res
                return res
            exch_pos = {}
            exch_cash = None
            pid = None
            if exchange_book:
                fetch_ok = bool(exchange_book.get("ok", fetch_ok))
                fetch_error = str(exchange_book.get("error") or fetch_error)
                exch_pos = dict(exchange_book.get("positions") or {})
                exch_cash = exchange_book.get("cash_usd")
                pid = exchange_book.get("portfolio_id")
            res = compare_books(
                local_positions=self.positions,
                local_cash=self.cash,
                exchange_positions=exch_pos,
                exchange_cash=exch_cash,
                portfolio_id=pid,
                fetch_ok=fetch_ok,
                fetch_error=fetch_error,
            )
            surplus = 0.0 if self.dry_run else venue_cash_delta(res)
            if surplus > 0:
                self.deposit_cash(surplus, reason="venue_deposit")
                res = compare_books(
                    local_positions=self.positions,
                    local_cash=self.cash,
                    exchange_positions=exch_pos,
                    exchange_cash=exch_cash,
                    portfolio_id=pid,
                    fetch_ok=fetch_ok,
                    fetch_error=fetch_error,
                )
            elif surplus < 0:
                self.withdraw_cash(-surplus, reason="venue_withdraw")
                res = compare_books(
                    local_positions=self.positions,
                    local_cash=self.cash,
                    exchange_positions=exch_pos,
                    exchange_cash=exch_cash,
                    portfolio_id=pid,
                    fetch_ok=fetch_ok,
                    fetch_error=fetch_error,
                )
            elif (
                not self.dry_run
                and res.fetch_ok
                and any(not str(m).startswith("USD:") for m in (res.mismatches or []))
            ):
                self._book_sync_from_exchange(exch_pos, exch_cash, reason="venue_lots")
                res = compare_books(
                    local_positions=self.positions,
                    local_cash=self.cash,
                    exchange_positions=exch_pos,
                    exchange_cash=exch_cash,
                    portfolio_id=pid,
                    fetch_ok=fetch_ok,
                    fetch_error=fetch_error,
                )
            self._last_venue_sync_mono = time.monotonic()
            self._reconciled = bool(res.ok)
            self._last_reconcile = res
            self._journal({"action": "RECONCILE", **res.as_dict()})
            if not res.ok:
                self._journal(
                    {
                        "action": "RECONCILE_MISMATCH",
                        "mismatches": res.mismatches,
                        "reason": res.reason,
                    }
                )
            return res

    def sync_exchange_book(
        self,
        *,
        min_interval_sec: float = 60.0,
        fetch=None,
        portfolio_id: str | None = None,
    ) -> ReconcileResult | None:
        """Re-read the live sleeve and adopt venue cash increases. Throttled. No restart."""
        if self.dry_run:
            return None
        now = time.monotonic()
        if self._last_venue_sync_mono and (
            now - self._last_venue_sync_mono
        ) < float(min_interval_sec):
            return None
        from strategies.live_reconcile import fetch_coinbase_book

        fn = fetch or fetch_coinbase_book
        pid = (
            portfolio_id
            if portfolio_id is not None
            else os.getenv("COINBASE_PORTFOLIO_ID")
        )
        try:
            book = fn(pid)
        except Exception as e:
            book = {
                "ok": False,
                "error": str(e),
                "positions": {},
                "cash_usd": None,
            }
        return self.apply_reconcile(book)

    def _resync_after_order_attempt(self) -> None:
        """Re-read venue SSOT after a live attempt that did not book. Fail closed if no fetch."""
        fetch = self._venue_book_fetch
        if fetch is None:
            self._reconciled = False
            return
        rec = self.sync_exchange_book(min_interval_sec=0.0, fetch=fetch)
        if rec is None:
            self._reconciled = False

    def _journal(self, entry: dict):
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        entry["mode"] = self.mode_label
        entry["dry_run"] = self.dry_run
        with open(self._journal_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def deposit_cash(self, amount: float, reason: str = "manual_deposit") -> dict:
        """Add cash and journal CASH_DEPOSIT. Restart-safe. Does not place trades."""
        amt = float(amount)
        if amt <= 0:
            raise ValueError("deposit amount must be positive")
        with self._lock:
            self.cash = float(self.cash) + amt
            self.start_usd = float(self.start_usd) + amt
            cap = raise_total_notional_for_contributed(self.limits, self.start_usd)
            eq = self._equity_unlocked()
            entry = {
                "action": "CASH_DEPOSIT",
                "amount": amt,
                "reason": reason,
                "cash": self.cash,
                "equity": eq,
                "start_usd": self.start_usd,
                "max_total_notional_usd": cap,
            }
            self._journal(entry)
            return {
                "amount": amt,
                "cash": round(self.cash, 6),
                "equity": round(eq, 6),
                "start_usd": round(self.start_usd, 6),
                "reason": reason,
            }

    def withdraw_cash(self, amount: float, reason: str = "venue_withdraw") -> dict:
        amt = float(amount)
        if amt <= 0:
            raise ValueError("withdraw amount must be positive")
        with self._lock:
            amt = min(amt, float(self.cash))
            self.cash = float(self.cash) - amt
            self.start_usd = max(0.0, float(self.start_usd) - amt)
            raise_total_notional_for_contributed(self.limits, self.start_usd)
            eq = self._equity_unlocked()
            entry = {
                "action": "CASH_WITHDRAW",
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

    def _book_sync_from_exchange(
        self, exchange_positions: dict, exchange_cash: float | None, reason: str
    ) -> None:
        """Adopt venue lots + cash. Keep local entry when the name already exists."""
        new_pos: dict = {}
        exch = exchange_positions or {}
        local_by_base = {str(k).upper(): (k, v) for k, v in self.positions.items()}
        for raw_sym, qty in exch.items():
            base = str(raw_sym).replace("/", "-").upper()
            if base.endswith("-USD"):
                base = base[:-4]
            qty_f = float(qty or 0.0)
            if qty_f <= QTY_EPS:
                continue
            prev_key, prev = local_by_base.get(base, (base, {}))
            entry_px = float((prev or {}).get("entry") or self.last_price.get(prev_key) or 0.0)
            new_pos[str(prev_key).upper() if prev else base] = {
                "side": "LONG",
                "entry": entry_px,
                "qty": qty_f,
                "ts": int(time.time() * 1e9),
                "fee": float((prev or {}).get("fee") or 0.0),
            }
        self.positions = new_pos
        if exchange_cash is not None:
            self.cash = float(exchange_cash)
        self._journal(
            {
                "action": "BOOK_SYNC",
                "reason": reason,
                "cash": self.cash,
                "positions": {
                    k: {
                        "qty": v["qty"],
                        "entry": v["entry"],
                        "fee": float(v.get("fee") or 0.0),
                    }
                    for k, v in new_pos.items()
                },
            }
        )

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

    def _position_notional_total(self) -> float:
        total = 0.0
        for sym, p in self.positions.items():
            px = self.last_price.get(sym, p["entry"])
            total += abs(p["qty"] * px)
        return total

    def _roll_day_unlocked(self) -> None:
        eq = self._equity_unlocked()
        if self.day.roll(eq):
            self._journal(
                {
                    "action": "DAY_OPEN",
                    "day": self.day.day_utc.isoformat() if self.day.day_utc else None,
                    "equity": eq,
                }
            )
            self._daily_halt_logged_day = None

    def _daily_loss(self) -> float:
        return self.day.daily_loss_usd(self._equity_unlocked())

    def apply(self, actions: list[dict]):
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

    def _flatten_halt_unlocked(self, why: str) -> None:
        if self._halt_flatten_done and not self.positions:
            return
        self._journal({"action": "HALT_ENGAGED", "reason": why, "n_positions": len(self.positions)})
        for sym in list(self.positions):
            px = float(self.last_price.get(sym) or self.positions[sym]["entry"])
            # _close re-acquires RLock
            self._close(sym, px, reason=f"halt_flatten:{why}")
        self._halt_flatten_done = True

    def flatten_for_halt(self, why: str = "TRADING_HALTED") -> None:
        with self._lock:
            self._flatten_halt_unlocked(why)

    def _call_cfg(self) -> dict:
        return self._bridge.bridge_config(self._journal_path) | {"dry_run": self.dry_run}

    def _recover_fill(self, symbol_usd: str, coid: str, qty: float, price: float) -> dict | None:
        query = getattr(self._bridge, "query_order", None)
        if query is None:
            return None
        try:
            found = query(symbol_usd, coid, journal_path=self._journal_path, cfg=self._call_cfg())
        except Exception:
            found = None
        if not found:
            return None
        if found.get("open"):
            cancel = getattr(self._bridge, "cancel_order", None)
            if cancel:
                try:
                    cancel(symbol_usd, coid, cfg=self._call_cfg())
                except Exception:
                    pass
            try:
                found = query(symbol_usd, coid, journal_path=self._journal_path, cfg=self._call_cfg()) or found
            except Exception:
                pass
        filled = float(found.get("filled") or 0.0)
        if filled <= QTY_EPS:
            return None
        found.setdefault("client_order_id", coid)
        self._journal(
            {
                "action": "ORDER_RECOVERED",
                "symbol": symbol_usd,
                "client_order_id": coid,
                "filled": filled,
                "avg_price": found.get("avg_price"),
                "cost": found.get("cost"),
                "fee": found.get("fee"),
                "status": found.get("status"),
            }
        )
        return found

    def _fill_economics(self, fill: dict, request_price: float):
        """Return (ok, qty, px, cost, fee). Live refuses missing venue economics."""
        qty = float(fill.get("filled") or 0.0)
        if qty <= QTY_EPS:
            return False, 0.0, None, None, None
        if self.dry_run:
            px = float(fill.get("avg_price") or request_price)
            raw_cost = fill.get("cost")
            cost = float(raw_cost) if raw_cost is not None else qty * px
            raw_fee = fill.get("fee")
            fee = float(raw_fee) if raw_fee is not None else cost * self.fee_rate
            return True, qty, px, cost, fee
        raw_cost = fill.get("cost")
        raw_fee = fill.get("fee")
        raw_px = fill.get("avg_price")
        if raw_cost is None or raw_fee is None:
            return False, qty, raw_px, raw_cost, raw_fee
        cost = float(raw_cost)
        fee = float(raw_fee)
        if raw_px is not None:
            px = float(raw_px)
        else:
            px = cost / qty
        return True, qty, px, cost, fee

    def _submit(
        self,
        fn,
        symbol_usd: str,
        qty: float,
        price: float,
        coid: str,
        side: str,
        *,
        defer_insufficient: bool = False,
    ) -> dict | None:
        self._last_submit_error = None
        self._journal(
            {
                "action": "ORDER_SUBMIT",
                "symbol": symbol_usd,
                "side": side,
                "qty": qty,
                "price": price,
                "client_order_id": coid,
            }
        )
        try:
            return fn(
                symbol_usd,
                qty,
                price=price,
                client_order_id=coid,
                journal_path=self._journal_path,
                cfg=self._call_cfg(),
            )
        except Exception as e:
            self._last_submit_error = str(e)
            recovered = None if self.dry_run else self._recover_fill(symbol_usd, coid, qty, price)
            if recovered:
                self._last_submit_error = None
                return recovered
            insufficient_buy = side == "buy" and _is_insufficient_fund(e)
            if not (defer_insufficient and insufficient_buy):
                self._journal(
                    {
                        "action": "ORDER_ERROR",
                        "symbol": symbol_usd,
                        "side": side,
                        "error": str(e),
                        "client_order_id": coid,
                    }
                )
            if _is_venue_down(e):
                self._venue_down_until = time.time() + VENUE_DOWN_COOLDOWN_SEC
                self._venue_down_logged = True
                self._journal(
                    {
                        "action": "EXEC_UNAVAILABLE",
                        "symbol": symbol_usd,
                        "side": side,
                        "error": str(e),
                        "cooldown_sec": VENUE_DOWN_COOLDOWN_SEC,
                    }
                )
            return None

    def _buy_protect_limit(self, symbol: str, close: float, reason: str):
        """L1 + spread/chase gates. None = skip (already journaled). Else (limit_px, submit_fn)."""
        fetch_l1 = getattr(self._bridge, "fetch_l1", None)
        book = None
        err = None
        if fetch_l1 is None:
            err = "fetch_l1 missing"
        else:
            try:
                book = fetch_l1(
                    f"{symbol}-USD",
                    cfg=self._call_cfg(),
                    journal_path=self._journal_path,
                )
            except Exception as e:
                err = str(e)
                book = None
        if err is not None or not isinstance(book, dict):
            self._journal(
                {
                    "action": "BUY_SKIP_BOOK",
                    "symbol": symbol,
                    "reason_signal": reason,
                    "error": err or "missing L1",
                }
            )
            return None
        bid = book.get("bid")
        ask = book.get("ask")
        if bid is None or ask is None:
            self._journal(
                {
                    "action": "BUY_SKIP_BOOK",
                    "symbol": symbol,
                    "reason_signal": reason,
                    "error": "missing L1",
                    "bid": bid,
                    "ask": ask,
                    "close": close,
                }
            )
            return None
        d = decide_buy(
            bid,
            ask,
            close=close,
            max_spread_bps=self._buy_protect_max_spread_bps,
            chase_cap_bps=self._buy_protect_chase_cap_bps,
        )
        if d.skip:
            action = {
                "spread": "BUY_SKIP_SPREAD",
                "chase": "BUY_SKIP_CHASE",
            }.get(d.reason or "", "BUY_SKIP_BOOK")
            self._journal(
                {
                    "action": action,
                    "symbol": symbol,
                    "reason_signal": reason,
                    "bid": d.bid,
                    "ask": d.ask,
                    "spread_bps": d.spread_bps,
                    "close": close,
                }
            )
            return None
        px = d.limit_price
        if px is None or px <= 0:
            self._journal(
                {
                    "action": "BUY_SKIP_BOOK",
                    "symbol": symbol,
                    "reason_signal": reason,
                    "bid": d.bid,
                    "ask": d.ask,
                    "spread_bps": d.spread_bps,
                    "close": close,
                    "error": "invalid limit_price",
                }
            )
            return None
        return float(px), self._bridge.limit_buy_ioc

    def _size_buy(self, target: float, px: float, fee_pad: float) -> tuple[float, float]:
        """Qty and notional that fit cash after fee_pad, capped by the clip."""
        if target <= 0 or px <= 0 or fee_pad < 0 or self.cash <= 0:
            return 0.0, 0.0
        max_spend = min(float(target) * (1.0 + fee_pad), float(self.cash))
        if max_spend <= 0:
            return 0.0, 0.0
        qty = floor_qty((max_spend / (1.0 + fee_pad)) / px)
        if qty <= 0:
            return 0.0, 0.0
        if qty * px * (1.0 + fee_pad) > float(self.cash) + 1e-6:
            qty = qty_within_fee_pad(self.cash, px, fee_pad)
        if qty <= 0:
            return 0.0, 0.0
        return qty, qty * px

    def _retry_buy_for_funds(
        self,
        buy_fn,
        symbol: str,
        px: float,
        qty: float,
        notional: float,
        coid: str,
        fee_pad: float,
        target: float,
        reason: str,
    ):
        """One smaller buy after PREVIEW_INSUFFICIENT_FUND. No fill was booked."""
        venue = float(self.venue_taker_fee_rate)
        retry_pad = venue if fee_pad + 1e-12 < venue else fee_pad + venue
        retry_qty, retry_notional = self._size_buy(target, px, retry_pad)
        min_n = float(getattr(self.limits, "min_notional_usd", 0.0) or 0.0)
        err = getattr(self, "_last_submit_error", None)
        if (
            retry_qty <= 0
            or retry_qty >= qty - 1e-12
            or retry_notional + 1e-9 < min_n
        ):
            self._journal(
                {
                    "action": "ORDER_ERROR",
                    "symbol": f"{symbol}-USD",
                    "side": "buy",
                    "error": err,
                    "client_order_id": coid,
                }
            )
            return None, qty, notional, coid
        self._journal(
            {
                "action": "BUY_RESIZE_INSUFFICIENT_FUND",
                "symbol": symbol,
                "side": "buy",
                "qty": qty,
                "retry_qty": retry_qty,
                "price": px,
                "cash": self.cash,
                "fee_pad": retry_pad,
                "reason_signal": reason,
                "error": err,
            }
        )
        coid = f"buy-{symbol}-{uuid.uuid4().hex[:12]}"
        fill = self._submit(buy_fn, f"{symbol}-USD", retry_qty, px, coid, "buy")
        return fill, retry_qty, retry_notional, coid

    def _buy(self, symbol: str, price: float, reason: str):
        with self._lock:
            self._roll_day_unlocked()
            if symbol in self.positions:
                return
            now = time.time()
            if self._venue_down_until and now < self._venue_down_until:
                if not self._venue_down_logged:
                    self._journal(
                        {
                            "action": "EXEC_UNAVAILABLE",
                            "symbol": symbol,
                            "side": "buy",
                            "until": self._venue_down_until,
                            "reason_signal": reason,
                        }
                    )
                    self._venue_down_logged = True
                return
            if not self._reconciled:
                self._journal(
                    {
                        "action": "RISK_DENY",
                        "symbol": symbol,
                        "side": "buy",
                        "reasons": ["unreconciled with exchange"],
                        "reason_signal": reason,
                    }
                )
                return
            fetch = self._venue_book_fetch
            if fetch is not None:
                rec = self.sync_exchange_book(min_interval_sec=0.0, fetch=fetch)
                if rec is None or not rec.ok:
                    self._journal(
                        {
                            "action": "RISK_DENY",
                            "symbol": symbol,
                            "side": "buy",
                            "reasons": ["unreconciled with exchange"],
                            "reason_signal": reason,
                        }
                    )
                    return
            px = float(price)
            buy_fn = self._bridge.market_buy
            if self._buy_protect_enabled and not self.dry_run:
                gated = self._buy_protect_limit(symbol, price, reason)
                if gated is None:
                    return
                px, buy_fn = gated
            eq = self._equity_unlocked()
            dloss = self._daily_loss()
            halt_usd = effective_daily_loss_halt(self.limits, self.day.day_start_equity)
            if halt_usd is not None and dloss >= halt_usd:
                if self._daily_halt_logged_day != self.day.day_utc:
                    self._journal(
                        {
                            "action": "DAILY_LOSS_HALT",
                            "daily_loss_usd": dloss,
                            "day_start_equity": self.day.day_start_equity,
                            "equity": eq,
                            "halt_usd": halt_usd,
                        }
                    )
                    self._daily_halt_logged_day = self.day.day_utc
            cap = float(getattr(self.limits, "max_position_notional_usd", 0.0) or 0.0)
            target = clip_target_usd(eq, self.max_alloc_pct, cap)
            if target <= 0 or px <= 0 or self.cash <= 0:
                return
            fee_pad = float(self.fee_rate)
            if not self.dry_run:
                fee_pad = max(fee_pad, float(self.venue_taker_fee_rate))
            qty, notional = self._size_buy(target, px, fee_pad)
            if qty <= 0:
                return
            cost = notional * (1.0 + fee_pad)
            if cost > self.cash + 1e-9:
                self._journal(
                    {
                        "action": "RISK_DENY",
                        "symbol": symbol,
                        "side": "buy",
                        "reasons": [f"cash ${self.cash:.4f} < cost ${cost:.4f}"],
                        "reason_signal": reason,
                    }
                )
                return

            risk = check_order(
                symbol=f"{symbol}-USD",
                side="buy",
                notional_usd=notional,
                product="spot",
                current_total_notional=self._position_notional_total(),
                daily_loss_usd=dloss,
                open_positions=self._open_longs(),
                equity=eq,
                is_close=False,
                limits=self.limits,
                day_start_equity=self.day.day_start_equity,
            )
            if not risk.approved:
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

            coid = f"buy-{symbol}-{uuid.uuid4().hex[:12]}"
            fill = self._submit(
                buy_fn,
                f"{symbol}-USD",
                qty,
                px,
                coid,
                "buy",
                defer_insufficient=not self.dry_run,
            )
            if (
                not fill
                and not self.dry_run
                and _is_insufficient_fund(getattr(self, "_last_submit_error", None))
            ):
                fill, qty, notional, coid = self._retry_buy_for_funds(
                    buy_fn, symbol, px, qty, notional, coid, fee_pad, target, reason
                )
            if not fill:
                return
            ok, fill_qty, fill_px, notional, fee = self._fill_economics(fill, px)
            if fill_qty <= QTY_EPS:
                self._journal(
                    {
                        "action": "ORDER_EMPTY_FILL",
                        "symbol": symbol,
                        "side": "buy",
                        "client_order_id": coid,
                    }
                )
                if not self.dry_run:
                    self._resync_after_order_attempt()
                return
            if not ok:
                self._journal(
                    {
                        "action": "ORDER_INCOMPLETE_FILL",
                        "symbol": symbol,
                        "side": "buy",
                        "filled": fill_qty,
                        "avg_price": fill_px,
                        "cost": notional,
                        "fee": fee,
                        "client_order_id": coid,
                        "order_id": fill.get("order_id"),
                    }
                )
                self._reconciled = False
                return
            cost = notional + fee
            if cost > self.cash + 1e-9:
                # Live fill already happened — book it and halt new buys.
                self._journal(
                    {
                        "action": "CASH_OVERDRAFT",
                        "symbol": symbol,
                        "cost": cost,
                        "cash": self.cash,
                        "client_order_id": coid,
                    }
                )
                self._reconciled = False
            self.cash -= cost
            self.positions[symbol] = {
                "side": "LONG",
                "entry": fill_px,
                "qty": fill_qty,
                "ts": int(time.time() * 1e9),
                "fee": fee,
                "client_order_id": coid,
            }
            self.trades += 1
            self._journal(
                {
                    "action": "LIVE_BUY" if not self.dry_run else "SHADOW_BUY",
                    "symbol": symbol,
                    "price": fill_px,
                    "qty": fill_qty,
                    "notional": notional,
                    "fee": fee,
                    "reason": reason,
                    "cash": self.cash,
                    "equity": self._equity_unlocked(),
                    "client_order_id": coid,
                    "order_id": fill.get("order_id"),
                    "risk": risk.reasons,
                }
            )

    def _close(self, symbol: str, price: float, reason: str):
        with self._lock:
            self._roll_day_unlocked()
            p = self.positions.get(symbol)
            if not p:
                return
            qty = float(p["qty"])
            if not self.dry_run and self._venue_book_fetch is not None:
                rec = self.sync_exchange_book(
                    min_interval_sec=0.0, fetch=self._venue_book_fetch
                )
                p = self.positions.get(symbol)
                if not p:
                    return
                qty = float(p["qty"])
                rec = rec or self._last_reconcile
                if rec is not None:
                    avail = (rec.exchange_positions or {}).get(str(symbol).upper())
                    if avail is not None:
                        qty = clip_sell_qty(qty, avail)
            if qty <= QTY_EPS:
                return
            notional = qty * price
            risk = check_order(
                symbol=f"{symbol}-USD",
                side="sell",
                notional_usd=notional,
                product="spot",
                current_total_notional=self._position_notional_total(),
                daily_loss_usd=self._daily_loss(),
                open_positions=self._open_longs(),
                equity=self._equity_unlocked(),
                is_close=True,
                limits=self.limits,
            )
            if not risk.approved:
                self._journal(
                    {
                        "action": "RISK_DENY",
                        "symbol": symbol,
                        "side": "sell_close",
                        "reasons": risk.reasons,
                        "reason_signal": reason,
                    }
                )
                return

            coid = f"cls-{symbol}-{uuid.uuid4().hex[:12]}"
            fill = self._submit(self._bridge.close_long, f"{symbol}-USD", qty, price, coid, "sell_close")
            if not fill:
                return
            ok, fill_qty, fill_px, notional, fee = self._fill_economics(fill, price)
            if fill_qty <= QTY_EPS:
                self._journal(
                    {
                        "action": "ORDER_EMPTY_FILL",
                        "symbol": symbol,
                        "side": "sell_close",
                        "client_order_id": coid,
                    }
                )
                return
            if not ok:
                self._journal(
                    {
                        "action": "ORDER_INCOMPLETE_FILL",
                        "symbol": symbol,
                        "side": "sell_close",
                        "filled": fill_qty,
                        "avg_price": fill_px,
                        "cost": notional,
                        "fee": fee,
                        "client_order_id": coid,
                        "order_id": fill.get("order_id"),
                    }
                )
                self._reconciled = False
                return
            if fill_qty > qty + 1e-9:
                fill_qty = qty
            entry_fee = float(p.get("fee") or 0.0) * (fill_qty / qty if qty else 1.0)
            pnl = (fill_px - p["entry"]) * fill_qty - fee - entry_fee
            self.cash += notional - fee
            self.day.add_realized(pnl)
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1
            remaining = qty - fill_qty
            partial = remaining > QTY_EPS
            if partial:
                remain_fee = float(p.get("fee") or 0.0) - entry_fee
                self.positions[symbol] = {
                    **p,
                    "qty": remaining,
                    "fee": max(0.0, remain_fee),
                }
            else:
                del self.positions[symbol]
            tag = "LIVE" if not self.dry_run else "SHADOW"
            action = f"{tag}_CLOSE_PARTIAL" if partial else f"{tag}_CLOSE"
            self._journal(
                {
                    "action": action,
                    "symbol": symbol,
                    "price": fill_px,
                    "qty": fill_qty,
                    "remaining_qty": remaining if partial else 0.0,
                    "notional": notional,
                    "pnl": pnl,
                    "fee": fee,
                    "reason": reason,
                    "cash": self.cash,
                    "equity": self._equity_unlocked(),
                    "client_order_id": coid,
                    "order_id": fill.get("order_id"),
                    "risk": risk.reasons,
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
                    }
                    for sym, p in self.positions.items()
                },
                "n_positions": len(self.positions),
                "open_longs": self._open_longs(),
                "trades": self.trades,
                "wins": self.wins,
                "losses": self.losses,
                "return_pct": round((eq / self.start_usd - 1) * 100, 2)
                if self.start_usd
                else 0.0,
                "dry_run": self.dry_run,
                "mode": self.mode_label,
                "backend": self.backend_name,
                "daily_realized_pnl": round(self.day.daily_realized_pnl, 2),
                "daily_loss_usd": round(self._daily_loss(), 2),
                "reconciled": self._reconciled,
                **self.day.snapshot(),
            }
