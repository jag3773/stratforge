#!/usr/bin/env python3
"""P0 live-safety tests. Never places real orders. Never creates LIVE_APPROVED."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"
os.environ.pop("TRADING_HALTED", None)
os.environ["LIVE_APPROVAL_REQUIRED"] = "false"  # must be ignored


class FakeBridge:
    def __init__(self, partial_frac: float = 1.0, fail: Exception | None = None, recover=None):
        self.partial_frac = partial_frac
        self.fail = fail
        self.recover = recover
        self.buys = 0
        self.sells = 0

    def bridge_config(self, journal_path=None):
        return {"dry_run": True, "approval_ok": True, "approval_reason": "dry-run", "journal_path": journal_path}

    def market_buy(self, symbol, qty, **kw):
        self.buys += 1
        if self.fail:
            raise self.fail
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
        if self.fail:
            raise self.fail
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
        return self.recover


class VenueBridge:
    """Live-shaped fills. dry_run=False so executor takes the venue-cost path."""

    def __init__(self, fill=None, sell_fill=None, incomplete=False):
        self.incomplete = incomplete
        self.fill = fill
        self.sell_fill = sell_fill
        self.buys = 0
        self.sells = 0

    def bridge_config(self, journal_path=None):
        return {
            "dry_run": False,
            "approval_ok": True,
            "approval_reason": "test-venue",
            "journal_path": journal_path,
        }

    def market_buy(self, symbol, qty, **kw):
        self.buys += 1
        if self.incomplete:
            return {
                "action": "LIVE_ORDER",
                "status": None,
                "filled": 0.0,
                "avg_price": None,
                "cost": None,
                "fee": None,
                "complete": False,
                "client_order_id": kw.get("client_order_id"),
            }
        if self.fill is not None:
            return dict(self.fill)
        # Today's PUMP venue fill (not the requested clip).
        return {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": 12128.0,
            "avg_price": 0.005092,
            "cost": 61.755776,
            "fee": 0.741069312,
            "complete": True,
            "order_id": "b2e999b2-6f2a-450f-a466-12d926a0b818",
            "client_order_id": kw.get("client_order_id"),
        }

    def close_long(self, symbol, qty, **kw):
        self.sells += 1
        self.last_sell_qty = float(qty)
        if self.sell_fill is not None:
            return dict(self.sell_fill)
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


def test_partial_close_keeps_remainder():
    with tempfile.TemporaryDirectory() as td:
        br = FakeBridge(partial_frac=0.4)
        ex = _ex(td, bridge=br)
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        qty = ex.positions["BTC"]["qty"]
        ex.apply([{"type": "CLOSE", "symbol": "BTC", "price": 110.0, "reason": "x"}])
        assert "BTC" in ex.positions, "partial close must keep remainder"
        rem = ex.positions["BTC"]["qty"]
        assert abs(rem - qty * 0.6) < 1e-6, (rem, qty)
        text = Path(ex._journal_path).read_text()
        assert "SHADOW_CLOSE_PARTIAL" in text
        # restore
        ex2 = _ex(td, bridge=FakeBridge())
        info = ex2.restore_from_journal()
        assert info["restored"]
        assert "BTC" in ex2.positions
        assert abs(ex2.positions["BTC"]["qty"] - rem) < 1e-6
    print("ok partial close")


def test_unreconciled_blocks_buy():
    with tempfile.TemporaryDirectory() as td:
        ex = _ex(td)
        ex._reconciled = False
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert "unreconciled" in Path(ex._journal_path).read_text()
    print("ok unreconciled blocks buy")


def test_reconcile_fail_closed_on_bad_fetch():
    from strategies.live_reconcile import compare_books

    res = compare_books(
        local_positions={"BTC": {"qty": 1.0}},
        local_cash=100.0,
        exchange_positions={},
        exchange_cash=0.0,
        fetch_ok=False,
        fetch_error="timeout",
    )
    assert res.ok is False
    assert "fetch failed" in res.reason
    print("ok reconcile fail closed")


def test_reconcile_match_allows_buy():
    with tempfile.TemporaryDirectory() as td:
        ex = _ex(td)
        ex._reconciled = False
        rec = ex.apply_reconcile({"ok": True, "positions": {}, "cash_usd": 1000.0})
        assert rec.ok is True
        assert ex.reconciled is True
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" in ex.positions
    print("ok reconcile match")


def test_timeout_recovers_via_query():
    with tempfile.TemporaryDirectory() as td:
        rec = {
            "status": "filled",
            "filled": 2.0,
            "avg_price": 100.0,
            "cost": 200.0,
            "fee": 0.2,
            "client_order_id": "x",
            "open": False,
        }
        br = FakeBridge(fail=TimeoutError("timed out after 30s"), recover=rec)
        ex = _ex(td, bridge=br)
        # force non-dry recover path
        ex.dry_run = False
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        assert "BTC" in ex.positions
        assert abs(ex.positions["BTC"]["qty"] - 2.0) < 1e-9
        text = Path(ex._journal_path).read_text()
        assert "ORDER_SUBMIT" in text
        assert "ORDER_RECOVERED" in text
    print("ok timeout recover")


def test_timeout_no_order_does_not_book():
    with tempfile.TemporaryDirectory() as td:
        br = FakeBridge(fail=TimeoutError("timed out"), recover=None)
        ex = _ex(td, bridge=br)
        ex.dry_run = False
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        assert "BTC" not in ex.positions
        assert "ORDER_ERROR" in Path(ex._journal_path).read_text()
    print("ok timeout no book")


def test_venue_down_stops_spray():
    with tempfile.TemporaryDirectory() as td:
        br = FakeBridge(
            fail=RuntimeError("unable to load pem from configured key")
        )
        ex = _ex(td, bridge=br)
        ex.dry_run = False
        ex.update_price("BTC", 100.0)
        ex.update_price("ETH", 50.0)
        ex.apply(
            [
                {"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"},
                {"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"},
            ]
        )
        assert br.buys == 1
        assert "BTC" not in ex.positions
        assert "ETH" not in ex.positions
        text = Path(ex._journal_path).read_text()
        assert "ORDER_ERROR" in text
        assert "EXEC_UNAVAILABLE" in text
        assert text.count("ORDER_SUBMIT") == 1
    print("ok venue down stops spray")


def test_daily_mtm_halt():
    with tempfile.TemporaryDirectory() as td:
        ex = _ex(td)
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        # mark the open bag down hard vs day-open ~1000
        ex.update_price("BTC", 1.0)
        loss = ex.day.daily_loss_usd(ex.equity())
        assert loss >= 50, loss
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert "DAILY_LOSS_HALT" in Path(ex._journal_path).read_text() or "RISK_DENY" in Path(ex._journal_path).read_text()
    print("ok daily mtm halt")


def test_negative_cash_refused_before_submit():
    with tempfile.TemporaryDirectory() as td:
        br = FakeBridge()
        ex = _ex(td, bridge=br)
        ex.cash = 0.5
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        assert "BTC" not in ex.positions
        assert br.buys == 0
    print("ok no negative cash submit")


def test_halt_flattens_and_allows_close():
    from risk_limits import RiskLimits, check_order

    with tempfile.TemporaryDirectory() as td:
        halt = os.path.join(td, "HALT")
        lim = RiskLimits(
            max_position_notional_usd=500,
            max_total_notional_usd=2000,
            halt_file=halt,
            daily_loss_halt_usd=50,
        )
        from strategies.live_executor import LiveExecutor

        ex = LiveExecutor(
            start_usd=1000,
            journal_path=os.path.join(td, "j.jsonl"),
            dry_run=True,
            limits=lim,
            bridge_module=FakeBridge(),
        )
        ex.update_price("BTC", 100.0)
        ex.apply([{"type": "BUY", "symbol": "BTC", "price": 100.0, "reason": "t"}])
        assert "BTC" in ex.positions
        Path(halt).write_text("stop\n")
        close = check_order("BTC-USD", "sell", 10, is_close=True, limits=lim)
        assert close.approved, close
        buy = check_order("ETH-USD", "buy", 10, equity=1000, limits=lim)
        assert not buy.approved
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert "BTC" not in ex.positions
        text = Path(ex._journal_path).read_text()
        assert "HALT_ENGAGED" in text
        assert "SHADOW_CLOSE" in text
    print("ok halt flatten")


def test_approval_bypass_removed():
    import ccxt_bridge

    os.environ["LIVE_APPROVAL_REQUIRED"] = "false"
    ok, reason = ccxt_bridge.approval_ok()
    # Env cannot disable or relocate approval. If the file is present (micro LIVE
    # authorized), ok is True *because of the file*, not the env.
    if ccxt_bridge.DEFAULT_APPROVAL.is_file():
        assert ok is True
        assert "approval file" in reason
    else:
        assert ok is False
        assert "missing approval" in reason
    print("ok no approval bypass")


def test_approval_path_not_redirectable():
    import ccxt_bridge

    with tempfile.NamedTemporaryFile() as fake:
        os.environ["LIVE_APPROVAL_FILE"] = fake.name
        ok, reason = ccxt_bridge.approval_ok()
        # Env cannot point approval at another file.
        assert "LIVE_APPROVED" in reason
        assert fake.name not in reason
        assert ok is ccxt_bridge.DEFAULT_APPROVAL.is_file()
    os.environ.pop("LIVE_APPROVAL_FILE", None)
    print("ok approval path pinned")


def test_live_requires_portfolio_id():
    from strategies import live_node as ln
    import ccxt_bridge

    from strategies.config_load import load_config

    cfg = load_config(ROOT / "config" / "live_micro.json")
    os.environ["LIVE_TRADING"] = "true"
    os.environ["SIMULATION_MODE"] = "false"
    os.environ.pop("COINBASE_PORTFOLIO_ID", None)
    # load_live_secrets would reload portfolio from .env.live and (with
    # LIVE_APPROVED present) construct a real LIVE executor against the
    # production journal. Stub the loader so this stays a unit test.
    orig = ccxt_bridge.load_live_secrets
    ccxt_bridge.load_live_secrets = lambda: None
    try:
        ln._build_executor("live", cfg)
        raise AssertionError("should refuse")
    except SystemExit as e:
        msg = str(e)
        assert "PORTFOLIO" in msg or "refused" in msg.lower() or "LIVE" in msg
    finally:
        ccxt_bridge.load_live_secrets = orig
        os.environ["LIVE_TRADING"] = "false"
        os.environ["SIMULATION_MODE"] = "true"
    print("ok live portfolio required")


def test_paper_uses_same_risk_caps():
    from risk_limits import RiskLimits, limits_from_config
    from strategies.paper_executor import PaperExecutor

    from strategies.config_load import load_config

    cfg = load_config(ROOT / "config" / "live_paper.json")
    assert "risk" in cfg
    lim = limits_from_config(cfg)
    assert lim.max_concurrent == 8
    assert abs(lim.max_alloc_pct - 0.25) < 1e-9
    with tempfile.TemporaryDirectory() as td:
        tight = RiskLimits(
            max_position_notional_usd=1,
            max_total_notional_usd=1,
            min_notional_usd=5,
            halt_file=os.path.join(td, "nohalt"),
        )
        ex = PaperExecutor(start_usd=1000, journal_path=os.path.join(td, "p.jsonl"), limits=tight)
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" not in ex.positions
        assert "RISK_DENY" in Path(ex._journal_path).read_text()
    print("ok paper risk caps")


def test_cash_deposit_restores():
    with tempfile.TemporaryDirectory() as td:
        ex = _ex(td)
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        cash_before = ex.cash
        dep = ex.deposit_cash(250.0, reason="unit_test")
        assert abs(dep["cash"] - (cash_before + 250.0)) < 1e-6
        assert abs(ex.start_usd - 1250.0) < 1e-6
        ex2 = _ex(td, write_start=False)
        info = ex2.restore_from_journal()
        assert info["restored"]
        assert abs(ex2.cash - (cash_before + 250.0)) < 1e-6, (ex2.cash, cash_before)
        assert abs(ex2.start_usd - 1250.0) < 1e-6
        assert "ETH" in ex2.positions
    print("ok cash deposit restores")


def test_day_tracker_resets():
    from strategies.day_risk import DayRiskTracker

    tr = DayRiskTracker()
    t0 = datetime(2026, 8, 15, 23, 0, tzinfo=timezone.utc)
    assert tr.roll(1000.0, now=t0) is True
    tr.add_realized(-10)
    t1 = t0 + timedelta(hours=2)
    assert tr.roll(990.0, now=t1) is True
    assert tr.daily_realized_pnl == 0.0
    assert abs(tr.day_start_equity - 990.0) < 1e-9
    assert abs(tr.daily_loss_usd(940.0) - 50.0) < 1e-9
    print("ok day tracker")


def test_live_buy_books_venue_cost_not_estimate():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge()
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", start_usd=250.0)
        ex._reconciled = True
        cash0 = ex.cash
        ex.update_price("PUMP", 0.005126)
        ex.apply([{"type": "BUY", "symbol": "PUMP", "price": 0.005126, "reason": "breakout_long"}])
        assert "PUMP" in ex.positions
        assert abs(ex.positions["PUMP"]["qty"] - 12128.0) < 1e-9
        assert abs(ex.positions["PUMP"]["entry"] - 0.005092) < 1e-12
        spent = 61.755776 + 0.741069312
        assert abs((cash0 - ex.cash) - spent) < 1e-9, (cash0, ex.cash, cash0 - ex.cash)
        text = Path(ex._journal_path).read_text()
        assert "FEE_ESTIMATED" not in text
        buy = [json.loads(L) for L in text.splitlines() if '"LIVE_BUY"' in L][-1]
        assert abs(float(buy["notional"]) - 61.755776) < 1e-9
        assert abs(float(buy["fee"]) - 0.741069312) < 1e-9
        assert abs(float(buy["qty"]) - 12128.0) < 1e-9
    print("ok live venue cost booked")


def test_live_buy_incomplete_fill_does_not_debit():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge(incomplete=True)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", start_usd=250.0)
        ex._reconciled = True
        cash0 = ex.cash
        ex.update_price("PUMP", 0.005126)
        ex.apply([{"type": "BUY", "symbol": "PUMP", "price": 0.005126, "reason": "t"}])
        assert "PUMP" not in ex.positions
        assert abs(ex.cash - cash0) < 1e-12
        assert ex.reconciled is False
        text = Path(ex._journal_path).read_text()
        assert "LIVE_BUY" not in text
        assert "ORDER_INCOMPLETE_FILL" in text or "ORDER_EMPTY_FILL" in text
        assert "FEE_ESTIMATED" not in text
    print("ok incomplete fill no debit")


def test_live_buy_missing_fee_does_not_estimate():
    """Venue qty/px without fee/cost must not invent 10bps and debit."""
    with tempfile.TemporaryDirectory() as td:
        fill = {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": 12128.0,
            "avg_price": 0.005092,
            "cost": None,
            "fee": None,
            "complete": False,
        }
        br = VenueBridge(fill=fill)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", start_usd=250.0)
        ex._reconciled = True
        cash0 = ex.cash
        ex.update_price("PUMP", 0.005126)
        ex.apply([{"type": "BUY", "symbol": "PUMP", "price": 0.005126, "reason": "t"}])
        assert "PUMP" not in ex.positions
        assert abs(ex.cash - cash0) < 1e-12
        text = Path(ex._journal_path).read_text()
        assert "LIVE_BUY" not in text
        assert "FEE_ESTIMATED" not in text
        assert "ORDER_INCOMPLETE_FILL" in text
        assert ex.reconciled is False
    print("ok missing fee no estimate")


def test_empty_fill_resyncs_and_allows_next_buy():
    """IOC cancel with 0 fill must re-read the venue book so the rest of the wave can try."""
    with tempfile.TemporaryDirectory() as td:
        empty = {
            "action": "LIVE_ORDER",
            "status": "canceled",
            "filled": 0.0,
            "avg_price": None,
            "cost": 0.0,
            "fee": 0.0,
            "complete": True,
            "order_id": "empty-1",
        }
        filled = {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": 10.0,
            "avg_price": 5.0,
            "cost": 50.0,
            "fee": 0.05,
            "complete": True,
            "order_id": "fill-2",
        }
        br = VenueBridge(fill=empty)
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", start_usd=250.0)
        ex._reconciled = True
        fetches = {"n": 0}

        def fetch(_pid=None):
            fetches["n"] += 1
            return {"ok": True, "positions": {}, "cash_usd": float(ex.cash)}

        ex._venue_book_fetch = fetch
        ex.update_price("DOT", 5.0)
        ex.apply([{"type": "BUY", "symbol": "DOT", "price": 5.0, "reason": "t"}])
        assert "DOT" not in ex.positions
        text = Path(ex._journal_path).read_text()
        assert "ORDER_EMPTY_FILL" in text
        assert "LIVE_BUY" not in text
        assert "RECONCILE" in text
        assert ex.reconciled is True
        br.fill = filled
        ex.update_price("LTC", 5.0)
        ex.apply([{"type": "BUY", "symbol": "LTC", "price": 5.0, "reason": "t"}])
        assert "LTC" in ex.positions
        assert "unreconciled" not in Path(ex._journal_path).read_text()
        assert fetches["n"] >= 1
    print("ok empty fill resyncs next buy")


def _live_micro_ex(td, **kw):
    from risk_limits import RiskLimits

    lim = kw.pop(
        "limits",
        RiskLimits(
            max_position_notional_usd=0.0,
            max_total_notional_usd=0.0,
            max_alloc_pct=0.25,
            max_concurrent=8,
            daily_loss_halt_usd=0.0,
            daily_loss_halt_pct=0.10,
            min_notional_usd=5.0,
            long_only=True,
            halt_file=os.path.join(td, "nohalt"),
        ),
    )
    return _ex(
        td,
        bridge=kw.pop("bridge", VenueBridge()),
        dry_run=False,
        mode_label="LIVE",
        start_usd=kw.pop("start_usd", 250.0),
        limits=lim,
        **kw,
    )


def test_venue_cash_increase_adopts_without_restart():
    """the live sleeve USD up + matching lots → CASH_DEPOSIT, still reconciled."""
    with tempfile.TemporaryDirectory() as td:
        ex = _live_micro_ex(td)
        ex._reconciled = False
        rec = ex.apply_reconcile({"ok": True, "positions": {}, "cash_usd": 500.0})
        assert rec.ok, rec
        assert ex.reconciled is True
        assert abs(ex.cash - 500.0) < 1e-6
        assert abs(ex.start_usd - 500.0) < 1e-6
        text = Path(ex._journal_path).read_text()
        assert "CASH_DEPOSIT" in text
        assert "venue_deposit" in text
        assert "RECONCILE_MISMATCH" not in text
    print("ok venue cash adopt")


def test_venue_cash_decrease_adopts_withdraw():
    with tempfile.TemporaryDirectory() as td:
        ex = _live_micro_ex(td)
        rec = ex.apply_reconcile({"ok": True, "positions": {}, "cash_usd": 100.0})
        assert rec.ok, rec
        assert ex.reconciled is True
        assert abs(ex.cash - 100.0) < 1e-6
        assert abs(ex.start_usd - 100.0) < 1e-6
        text = Path(ex._journal_path).read_text()
        assert "CASH_WITHDRAW" in text
        assert "RECONCILE_MISMATCH" not in text
    print("ok venue cash withdraw")


def test_venue_lot_drift_book_syncs():
    """Exchange lots/cash are SSOT when the fetch is healthy."""
    with tempfile.TemporaryDirectory() as td:
        ex = _live_micro_ex(td)
        rec = ex.apply_reconcile(
            {"ok": True, "positions": {"BTC": 0.01}, "cash_usd": 200.0}
        )
        assert rec.ok, rec
        assert "BTC" in ex.positions
        assert abs(ex.positions["BTC"]["qty"] - 0.01) < 1e-9
        assert abs(ex.cash - 200.0) < 1e-6
        text = Path(ex._journal_path).read_text()
        assert "BOOK_SYNC" in text
        assert "RECONCILE_MISMATCH" not in text
    print("ok venue lot book sync")


def test_failed_fetch_does_not_adopt_cash():
    with tempfile.TemporaryDirectory() as td:
        ex = _live_micro_ex(td)
        rec = ex.apply_reconcile(
            {"ok": False, "error": "timeout", "positions": {}, "cash_usd": 500.0}
        )
        assert rec.ok is False
        assert abs(ex.cash - 250.0) < 1e-6
        assert "CASH_DEPOSIT" not in Path(ex._journal_path).read_text()
    print("ok fail-closed fetch no adopt")


def test_restore_keeps_contributed_after_deposit():
    with tempfile.TemporaryDirectory() as td:
        ex = _live_micro_ex(td, write_start=False)
        ex.deposit_cash(250.0, reason="manual")
        ex2 = _live_micro_ex(td, write_start=False)
        info = ex2.restore_from_journal()
        assert info["restored"]
        assert abs(ex2.start_usd - 500.0) < 1e-6
        assert abs(ex2.cash - 500.0) < 1e-6
    print("ok restore after deposit")


def test_percent_clip_not_dollar_cap():
    with tempfile.TemporaryDirectory() as td:
        fill = {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": 5.0,
            "avg_price": 50.0,
            "cost": 250.0,
            "fee": 0.25,
            "complete": True,
        }
        ex = _live_micro_ex(td, start_usd=1000.0, bridge=VenueBridge(fill=fill))
        ex._reconciled = True
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" in ex.positions
        notional = ex.positions["ETH"]["qty"] * ex.positions["ETH"]["entry"]
        assert notional > 200.0
        assert notional < 260.0
    print("ok percent clip")


def test_pre_buy_sync_adopts_venue_cash():
    with tempfile.TemporaryDirectory() as td:
        fill = {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": 5.0,
            "avg_price": 50.0,
            "cost": 250.0,
            "fee": 0.25,
            "complete": True,
        }
        ex = _live_micro_ex(td, start_usd=250.0, bridge=VenueBridge(fill=fill))
        ex._reconciled = True
        ex.cash = 10.0

        def fetch(pid=None):
            return {"ok": True, "positions": {}, "cash_usd": 1000.0}

        ex._venue_book_fetch = fetch
        ex.update_price("ETH", 50.0)
        ex.apply([{"type": "BUY", "symbol": "ETH", "price": 50.0, "reason": "t"}])
        assert "ETH" in ex.positions
        assert abs(ex.start_usd - 1240.0) < 1e-6
        notional = ex.positions["ETH"]["qty"] * ex.positions["ETH"]["entry"]
        assert notional > 200.0
        text = Path(ex._journal_path).read_text()
        assert "CASH_DEPOSIT" in text
        assert "venue_deposit" in text
    print("ok pre-buy venue cash")


def test_sync_exchange_book_throttles_fetch():
    with tempfile.TemporaryDirectory() as td:
        ex = _live_micro_ex(td)
        calls = []

        def fetch(pid=None):
            calls.append(pid)
            return {"ok": True, "positions": {}, "cash_usd": 250.0}

        a = ex.sync_exchange_book(fetch=fetch, min_interval_sec=60.0)
        b = ex.sync_exchange_book(fetch=fetch, min_interval_sec=60.0)
        assert a is not None and a.ok
        assert b is None
        assert len(calls) == 1
    print("ok venue sync throttle")


def test_live_close_books_venue_proceeds():
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge()
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", start_usd=250.0)
        ex._reconciled = True
        ex.update_price("PUMP", 0.005126)
        ex.apply([{"type": "BUY", "symbol": "PUMP", "price": 0.005126, "reason": "t"}])
        cash_after_buy = ex.cash
        sell = {
            "action": "LIVE_ORDER",
            "status": "closed",
            "filled": 12128.0,
            "avg_price": 0.005200,
            "cost": 63.0656,
            "fee": 0.40,
            "complete": True,
        }
        br.sell_fill = sell
        ex.update_price("PUMP", 0.005200)
        ex.apply([{"type": "CLOSE", "symbol": "PUMP", "price": 0.005200, "reason": "x"}])
        assert "PUMP" not in ex.positions
        assert abs((ex.cash - cash_after_buy) - (63.0656 - 0.40)) < 1e-6
        text = Path(ex._journal_path).read_text()
        assert "FEE_ESTIMATED" not in text
        close = [json.loads(L) for L in text.splitlines() if '"LIVE_CLOSE"' in L][-1]
        assert abs(float(close["notional"]) - 63.0656) < 1e-9
        assert abs(float(close["fee"]) - 0.40) < 1e-9
    print("ok live venue close")


def test_close_clips_sell_to_wallet_available():
    """XLM 2026-09-17: rounded book 1620.9435 > wallet 1620.94349082."""
    with tempfile.TemporaryDirectory() as td:
        br = VenueBridge()
        ex = _ex(td, bridge=br, dry_run=False, mode_label="LIVE", start_usd=500.0)
        ex._reconciled = True
        ex.positions["XLM"] = {
            "side": "LONG",
            "entry": 0.19113,
            "qty": 1620.9435,
            "fee": 3.72,
        }
        ex.cash = 470.98
        ex.update_price("XLM", 0.185893)

        def fetch(pid=None):
            return {
                "ok": True,
                "positions": {"XLM": 1620.94349082},
                "cash_usd": 470.98,
            }

        ex._venue_book_fetch = fetch
        ex.apply([{"type": "CLOSE", "symbol": "XLM", "price": 0.185893, "reason": "rotate_out"}])
        assert br.sells == 1
        assert br.last_sell_qty <= 1620.94349082
        assert br.last_sell_qty == 1620.94349082
        assert "XLM" not in ex.positions
    print("ok close clips to wallet")


if __name__ == "__main__":
    test_partial_close_keeps_remainder()
    test_unreconciled_blocks_buy()
    test_reconcile_fail_closed_on_bad_fetch()
    test_reconcile_match_allows_buy()
    test_timeout_recovers_via_query()
    test_timeout_no_order_does_not_book()
    test_venue_down_stops_spray()
    test_daily_mtm_halt()
    test_negative_cash_refused_before_submit()
    test_halt_flattens_and_allows_close()
    test_approval_bypass_removed()
    test_approval_path_not_redirectable()
    test_live_requires_portfolio_id()
    test_paper_uses_same_risk_caps()
    test_cash_deposit_restores()
    test_day_tracker_resets()
    test_live_buy_books_venue_cost_not_estimate()
    test_live_buy_incomplete_fill_does_not_debit()
    test_live_buy_missing_fee_does_not_estimate()
    test_empty_fill_resyncs_and_allows_next_buy()
    test_venue_cash_increase_adopts_without_restart()
    test_venue_cash_decrease_adopts_withdraw()
    test_venue_lot_drift_book_syncs()
    test_failed_fetch_does_not_adopt_cash()
    test_restore_keeps_contributed_after_deposit()
    test_percent_clip_not_dollar_cap()
    test_pre_buy_sync_adopts_venue_cash()
    test_sync_exchange_book_throttles_fetch()
    test_live_close_books_venue_proceeds()
    test_close_clips_sell_to_wallet_available()
    print("ALL P0 LIVE SAFETY PASS")
