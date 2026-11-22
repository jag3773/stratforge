#!/usr/bin/env python3
"""LIVE buy-protect wiring. Never places real orders. Never creates LIVE_APPROVED."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"
os.environ.pop("TRADING_HALTED", None)
os.environ["LIVE_APPROVAL_REQUIRED"] = "false"  # must be ignored

PROTECT = {"enabled": True, "max_spread_bps": 40, "chase_cap_bps": 15}


class FakeBridge:
    def __init__(self, partial_frac: float = 1.0):
        self.partial_frac = partial_frac
        self.buys = 0
        self.sells = 0

    def bridge_config(self, journal_path=None):
        return {
            "dry_run": True,
            "approval_ok": True,
            "approval_reason": "dry-run",
            "journal_path": journal_path,
        }

    def market_buy(self, symbol, qty, **kw):
        self.buys += 1
        filled = float(qty) * self.partial_frac
        return {
            "action": "DRY_RUN_ORDER",
            "status": "SIMULATED",
            "filled": filled,
            "avg_price": kw.get("price"),
            "fee": None,
            "client_order_id": kw.get("client_order_id"),
        }

    def close_long(self, symbol, qty, **kw):
        self.sells += 1
        filled = float(qty) * self.partial_frac
        return {
            "action": "DRY_RUN_ORDER",
            "status": "SIMULATED",
            "filled": filled,
            "avg_price": kw.get("price"),
            "fee": None,
            "client_order_id": kw.get("client_order_id"),
        }

    def query_order(self, symbol, client_order_id, **kw):
        return None


class VenueBridge:
    """Live-shaped fills. dry_run=False so executor takes the venue-cost path."""

    def __init__(self, bid=None, ask=None, fetch_error=None, fill=None):
        self.bid = bid
        self.ask = ask
        self.fetch_error = fetch_error
        self.fill = fill
        self.buys = 0
        self.sells = 0
        self.iocs = 0
        self.l1_calls: list = []
        self.ioc_calls: list = []

    def bridge_config(self, journal_path=None):
        return {
            "dry_run": False,
            "approval_ok": True,
            "approval_reason": "test-venue",
            "journal_path": journal_path,
        }

    def fetch_l1(self, symbol, cfg=None, journal_path=None):
        self.l1_calls.append(symbol)
        if self.fetch_error is not None:
            raise self.fetch_error
        if self.bid is None or self.ask is None:
            return {}
        bid = float(self.bid)
        ask = float(self.ask)
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}

    def market_buy(self, symbol, qty, **kw):
        self.buys += 1
        if self.fill is not None:
            return dict(self.fill)
        q = float(qty)
        px = float(kw.get("price") or 0.0)
        return {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": q,
            "avg_price": px,
            "cost": q * px,
            "fee": 0.50,
            "complete": True,
            "client_order_id": kw.get("client_order_id"),
        }

    def limit_buy_ioc(self, symbol, qty, **kw):
        self.iocs += 1
        self.ioc_calls.append(
            {"symbol": symbol, "qty": float(qty), "price": kw.get("price")}
        )
        if self.fill is not None:
            return dict(self.fill)
        q = float(qty)
        px = float(kw.get("price") or 0.0)
        return {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": q,
            "avg_price": px,
            "cost": q * px,
            "fee": 0.50,
            "complete": True,
            "client_order_id": kw.get("client_order_id"),
        }

    def close_long(self, symbol, qty, **kw):
        self.sells += 1
        q = float(qty)
        px = float(kw.get("price") or 0.0)
        cost = q * px
        return {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": q,
            "avg_price": px,
            "cost": cost,
            "fee": 0.50,
            "complete": True,
            "client_order_id": kw.get("client_order_id"),
        }

    def query_order(self, symbol, client_order_id, **kw):
        return None


def _ex(td, **kw):
    from risk_limits import RiskLimits
    from strategies.live_executor import LiveExecutor

    lim = kw.pop("limits", None) or RiskLimits(
        max_position_notional_usd=500,
        max_total_notional_usd=2000,
        max_alloc_pct=0.25,
        max_concurrent=8,
        daily_loss_halt_usd=50,
        long_only=True,
        halt_file=os.path.join(td, "nohalt"),
    )
    bridge = kw.pop("bridge", FakeBridge())
    params = dict(
        start_usd=1000,
        max_alloc_pct=0.25,
        journal_path=os.path.join(td, "j.jsonl"),
        dry_run=True,
        limits=lim,
        mode_label="SHADOW",
        bridge_module=bridge,
    )
    params.update(kw)
    return LiveExecutor(**params)


def _skip_rows(path: str, action: str) -> list[dict]:
    return [json.loads(L) for L in Path(path).read_text().splitlines() if f'"{action}"' in L]


def test_spread_skip_journals_and_does_not_order():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(bid=100.0, ask=100.5)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", buy_protect=PROTECT)
        ex._reconciled = True
        ex.update_price("ETH", 100.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 100.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert br.iocs == 0
        assert br.buys == 0
        rows = _skip_rows(ex._journal_path, "BUY_SKIP_SPREAD")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "ETH"
        assert rows[0]["bid"] == 100.0
        assert rows[0]["ask"] == 100.5
        assert rows[0]["close"] == 100.0
        assert rows[0]["spread_bps"] is not None
        assert rows[0]["reason_signal"] == "t"


def test_chase_skip_journals_and_does_not_order():
    with tempfile.TemporaryDirectory() as td:
        # close=100 → cap ask 100.15; ask 100.16 with a tight book
        br = VenueBridge(bid=100.14, ask=100.16)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", buy_protect=PROTECT)
        ex._reconciled = True
        ex.update_price("ETH", 100.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 100.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert br.iocs == 0
        assert br.buys == 0
        rows = _skip_rows(ex._journal_path, "BUY_SKIP_CHASE")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "ETH"
        assert rows[0]["bid"] == 100.14
        assert rows[0]["ask"] == 100.16
        assert rows[0]["close"] == 100.0
        assert rows[0]["spread_bps"] is not None


def test_take_uses_limit_buy_ioc_at_ask_not_market_buy():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(bid=100.0, ask=100.2)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", buy_protect=PROTECT)
        ex._reconciled = True
        ex.update_price("ETH", 100.1)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 100.1, "reason": "t"}])
        assert "ETH" in ex.positions
        assert br.iocs == 1
        assert br.buys == 0
        assert br.ioc_calls[0]["symbol"] == "ETH-USD"
        assert abs(float(br.ioc_calls[0]["price"]) - 100.2) < 1e-12
        assert abs(ex.positions["ETH"]["entry"] - 100.2) < 1e-12


def test_l1_fail_journals_buy_skip_book_no_orders():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(fetch_error=RuntimeError("ticker timeout"))
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", buy_protect=PROTECT)
        ex._reconciled = True
        ex.update_price("ETH", 100.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 100.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert br.iocs == 0
        assert br.buys == 0
        rows = _skip_rows(ex._journal_path, "BUY_SKIP_BOOK")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "ETH"
        assert rows[0]["reason_signal"] == "t"
        assert "ticker timeout" in str(rows[0].get("error", ""))


def test_dry_run_ignores_protect_and_uses_market_buy():
    with tempfile.TemporaryDirectory() as td:
        br = FakeBridge()
        ex = _ex(td, bridge=br, dry_run=True, buy_protect=PROTECT)
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" in ex.positions
        assert br.buys == 1
        assert not hasattr(br, "iocs") or getattr(br, "iocs", 0) == 0
        text = Path(ex._journal_path).read_text()
        assert "BUY_SKIP_" not in text
        assert "SHADOW_BUY" in text


def test_close_still_uses_close_long():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(bid=100.0, ask=100.2)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", buy_protect=PROTECT)
        ex._reconciled = True
        ex.update_price("ETH", 100.1)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 100.1, "reason": "t"}])
        assert "ETH" in ex.positions
        qty = ex.positions["ETH"]["qty"]
        ex.apply([{"type": "CLOSE", "symbol": "ETH", "price": 101.0, "reason": "x"}])
        assert "ETH" not in ex.positions
        assert br.sells == 1
        assert br.iocs == 1
        assert qty > 0


_INSUFFICIENT = RuntimeError(
    "coinbase {\"error\":\"INSUFFICIENT_FUND\","
    "\"preview_failure_reason\":\"PREVIEW_INSUFFICIENT_FUND\"}"
)


class _RejectThenFill(VenueBridge):
    """First IOC raises the venue insufficient-fund preview. Later calls fill."""

    def __init__(self, *a, reject_n=1, **kw):
        super().__init__(*a, **kw)
        self.reject_n = reject_n

    def limit_buy_ioc(self, symbol, qty, **kw):
        self.iocs += 1
        self.ioc_calls.append(
            {"symbol": symbol, "qty": float(qty), "price": kw.get("price")}
        )
        if self.iocs <= self.reject_n:
            raise _INSUFFICIENT
        q = float(qty)
        px = float(kw.get("price") or 0.0)
        return {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": q,
            "avg_price": px,
            "cost": q * px,
            "fee": q * px * 0.009,
            "complete": True,
            "client_order_id": kw.get("client_order_id"),
        }


def _cash_bound_live(td, bridge):
    """Clip larger than cash, so the buy is limited by USD on hand."""
    from risk_limits import RiskLimits

    lim = RiskLimits(
        max_position_notional_usd=0,
        max_total_notional_usd=0,
        max_alloc_pct=1.0,
        max_concurrent=8,
        daily_loss_halt_usd=0,
        daily_loss_halt_pct=0,
        min_notional_usd=5.0,
        long_only=True,
        halt_file=os.path.join(td, "nohalt"),
    )
    ex = _ex(
        td,
        bridge=bridge,
        dry_run=False,
        mode_label="LIVE",
        buy_protect=PROTECT,
        limits=lim,
        start_usd=528.408,
        max_alloc_pct=1.0,
        fee_rate=0.001,
    )
    ex._reconciled = True
    ex.cash = 528.408
    return ex


def test_insufficient_fund_retries_buy_once_with_venue_fee_margin():
    """Oct 27 case: cash $528.41, 10 bps pad, Coinbase holds the taker fee."""
    with tempfile.TemporaryDirectory() as td:
        br = _RejectThenFill(bid=296.0, ask=296.08)
        ex = _cash_bound_live(td, br)
        ex.apply([{"type": "BUY", "symbol": "BCH", "price": 296.08, "reason": "t"}])
        assert br.iocs == 2, br.ioc_calls
        first, second = br.ioc_calls
        assert second["qty"] < first["qty"]
        hold = second["qty"] * second["price"]
        assert hold * (1.0 + 0.012) <= ex.start_usd + 1e-6
        assert "BCH" in ex.positions
        assert abs(ex.positions["BCH"]["qty"] - second["qty"]) < 1e-8
        rows = _skip_rows(ex._journal_path, "BUY_RESIZE_INSUFFICIENT_FUND")
        assert len(rows) == 1
        assert rows[0]["side"] == "buy"


def test_insufficient_fund_does_not_retry_forever():
    with tempfile.TemporaryDirectory() as td:
        br = _RejectThenFill(bid=296.0, ask=296.08, reject_n=9)
        ex = _cash_bound_live(td, br)
        ex.apply([{"type": "BUY", "symbol": "BCH", "price": 296.08, "reason": "t"}])
        assert br.iocs == 2
        assert "BCH" not in ex.positions


def test_other_buy_error_does_not_resize():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(bid=296.0, ask=296.08)

        def _boom(symbol, qty, **kw):
            br.iocs += 1
            raise RuntimeError("coinbase timeout")

        br.limit_buy_ioc = _boom
        ex = _cash_bound_live(td, br)
        ex.apply([{"type": "BUY", "symbol": "BCH", "price": 296.08, "reason": "t"}])
        assert br.iocs == 1
        assert "BCH" not in ex.positions


def test_cash_bound_live_buy_reserves_venue_taker_fee_before_submit():
    """First order already leaves the 120 bps Coinbase hold. No reject required."""
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(bid=296.0, ask=296.08)
        ex = _cash_bound_live(td, br)
        ex.apply([{"type": "BUY", "symbol": "BCH", "price": 296.08, "reason": "t"}])
        assert br.iocs == 1
        call = br.ioc_calls[0]
        hold = call["qty"] * call["price"]
        assert hold * (1.0 + 0.012) <= 528.408 + 1e-6
        assert hold * (1.0 + 0.001) < 528.408 - 1.0
        assert "BCH" in ex.positions
