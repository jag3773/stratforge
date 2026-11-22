#!/usr/bin/env python3
"""Coinbase maintenance classifier: pause only when the trading path is hit."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")


def load():
    path = ROOT / "scripts" / "coinbase_maintenance.py"
    spec = importlib.util.spec_from_file_location("coinbase_maintenance", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load()
UNIVERSE = {"AERO", "BTC", "ETH", "ZEC"}


def inc(name, body, **kwargs):
    item = {
        "id": kwargs.pop("id", "abc"),
        "name": name,
        "status": kwargs.pop("status", "scheduled"),
        "scheduled_for": kwargs.pop("scheduled_for", "2026-11-28T13:00:00-05:00"),
        "scheduled_until": kwargs.pop("scheduled_until", "2026-11-28T21:00:00-05:00"),
        "incident_updates": [{"created_at": "2026-11-20T12:00:00-05:00", "body": body, "status": "scheduled"}],
        "components": kwargs.pop("components", []),
    }
    item.update(kwargs)
    return item


def test_system_maintenance_pauses():
    body = (
        "Coinbase is set to undergo scheduled system maintenance on Saturday, November 14 "
        "around 1pm ET, estimated to last up to 4 hours. During this time, some users may "
        "experience degraded performance across multiple products. Your funds are safe."
    )
    decision, reason = M.classify(
        inc("Scheduled System Maintenance — November 14", body),
        UNIVERSE,
        set(),
    )
    assert decision == "pause", reason


def test_india_bank_stays_up():
    body = (
        "India INR Network is set to undergo scheduled bank maintenance on October 9 "
        "from 12:15 AM to 12:30 AM IST. INR deposits via UPI may be delayed during the "
        "scheduled maintenance window. INR withdrawals and crypto trading are not impacted."
    )
    decision, reason = M.classify(inc("Scheduled Bank Maintenance - India (INR)", body), UNIVERSE, set())
    assert decision == "skip", reason
    assert not reason.startswith("postponed")


def test_unheld_asset_migration_stays_up():
    body = "VELO trading will be disabled on November 2, 2026. Trading of AERO will not be impacted."
    decision, reason = M.classify(inc("AERO and VELO migration", body), UNIVERSE, set())
    assert decision == "skip", reason


def test_held_asset_trading_disabled_pauses():
    body = "VELO trading will be disabled on November 2, 2026. Trading of AERO will not be impacted."
    decision, reason = M.classify(inc("AERO and VELO migration", body), UNIVERSE, {"VELO"})
    assert decision == "pause", reason
    assert "VELO" in reason


def test_universe_name_trading_disabled_pauses():
    decision, reason = M.classify(
        inc("ZEC migration", "ZEC trading will be disabled on December 1, 2026."),
        UNIVERSE,
        set(),
    )
    assert decision == "pause", reason


def test_advanced_trade_pauses():
    decision, reason = M.classify(
        inc("Scheduled Maintenance - Retail Advanced Trade", "Maintenance will begin as scheduled in 60 minutes."),
        UNIVERSE,
        set(),
    )
    assert decision == "pause", reason


def test_sends_only_stays_up():
    body = (
        "Coinbase is set to undergo system maintenance on October 24, 2026, from 10:00 AM to 10:30 AM ET. "
        "Sends and receives for Bitcoin (BTC) and Ethereum (ETH) will be delayed."
    )
    decision, reason = M.classify(inc("Scheduled Maintenance - Crypto Sends and Receives", body), UNIVERSE, set())
    assert decision == "skip", reason


def test_perpetuals_only_stays_up():
    decision, reason = M.classify(
        inc(
            "Coinbase - Advanced Trade - Perpetual Futures",
            "Perpetual futures will be briefly paused.",
            components=[{"name": "Coinbase - Advanced Trade - Perpetual Futures"}],
        ),
        UNIVERSE,
        set(),
    )
    assert decision == "skip", reason


def test_api_component_pauses():
    decision, reason = M.classify(
        inc("API maintenance", "Brief restart.", components=[{"name": "Coinbase - API"}]),
        UNIVERSE,
        set(),
    )
    assert decision == "pause", reason


def test_exchange_rest_component_pauses():
    decision, reason = M.classify(
        inc("Exchange REST", "Restart.", components=[{"name": "Coinbase Exchange REST API"}]),
        UNIVERSE,
        set(),
    )
    assert decision == "pause", reason


def test_postponed_system_window_stays_up():
    body = "The system upgrade has been postponed, and will no longer be on Saturday, November 14. A new date will be announced."
    decision, reason = M.classify(inc("Scheduled System Maintenance — November 14", body), UNIVERSE, set())
    assert decision == "skip", reason
    assert reason.startswith("postponed")


def test_rescheduled_system_window_pauses():
    body = "The maintenance has been rescheduled and moved to Saturday, November 28 at 1:00pm ET."
    decision, reason = M.classify(
        inc(
            "Scheduled System Maintenance — November 28",
            body,
            scheduled_for="2026-11-28T13:00:00-05:00",
        ),
        UNIVERSE,
        set(),
    )
    assert decision == "pause", reason


def test_unlabeled_maintenance_is_ambiguous():
    decision, reason = M.classify(inc("Scheduled maintenance", "We will be undergoing scheduled maintenance during this time."), UNIVERSE, set())
    assert decision == "ambiguous", reason


def test_stop_lead_and_in_progress():
    now = datetime(2026, 11, 28, 12, 0, tzinfo=ET)
    early = inc("Scheduled System Maintenance", "across multiple products", scheduled_for="2026-11-28T13:00:00-05:00")
    assert M.should_stop_now(now, early) is False
    close = datetime(2026, 11, 28, 12, 40, tzinfo=ET)
    assert M.should_stop_now(close, early) is True
    live = inc("Scheduled System Maintenance", "across multiple products", status="in_progress", scheduled_for="2026-11-28T18:00:00-05:00")
    assert M.should_stop_now(now, live) is True
    done = datetime(2026, 11, 28, 21, 5, tzinfo=ET)
    assert M.should_stop_now(done, early) is False


def test_resume_stays_down_until_window_ends_or_completes():
    stopped = datetime(2026, 11, 28, 12, 30, tzinfo=ET)
    until = datetime(2026, 11, 28, 21, 0, tzinfo=ET)
    mid = datetime(2026, 11, 28, 15, 0, tzinfo=ET)
    ok, why = M.resume_allowed(mid, stopped, until, "scheduled", True, 2)
    assert ok is False and why == "window still open"
    ok, why = M.resume_allowed(mid, stopped, until, "in_progress", True, 2)
    assert ok is False
    ok, why = M.resume_allowed(mid, stopped, until, "completed", True, 1)
    assert ok is True
    after = datetime(2026, 11, 28, 21, 10, tzinfo=ET)
    ok, why = M.resume_allowed(after, stopped, until, None, True, 1)
    assert ok is False and "second" in why
    ok, why = M.resume_allowed(after, stopped, until, None, True, 2)
    assert ok is True


def test_start_blocked_unless_clean_main():
    assert M.start_blocked("fix/ci-lite-unit-failures", "") is not None
    assert M.start_blocked("main", " M strategies/signal_engine.py") is not None
    assert M.start_blocked("main", "") is None


def test_user_facing_span_is_eastern():
    start = datetime(2026, 11, 28, 18, 0, tzinfo=ZoneInfo("UTC"))
    end = datetime(2026, 11, 29, 2, 0, tzinfo=ZoneInfo("UTC"))
    text = M.fmt_span(start, end)
    assert "UTC" not in text
    assert text.endswith("ET")
    assert "1:00pm" in text
    assert "9:00pm" in text


def test_stop_oneshot_is_not_recreated_for_the_same_minute():
    want = datetime(2026, 11, 28, 12, 30, tzinfo=ET)
    assert M.stop_oneshot_action(None, False, want) == "create"
    assert M.stop_oneshot_action(want.isoformat(), True, want) == "keep"
    assert M.stop_oneshot_action(want.isoformat(), False, want) == "create"
    moved = want + timedelta(hours=2)
    assert M.stop_oneshot_action(want.isoformat(), True, moved) == "edit"


def test_daily_tail_only_if_stopped_across_utc_midnight():
    stopped = datetime(2026, 11, 28, 18, 0, tzinfo=ET)
    after = datetime(2026, 11, 28, 20, 0, tzinfo=ET)
    assert M.daily_tail_if_due(after, stopped) is not None
    before = datetime(2026, 11, 28, 16, 0, tzinfo=ET)
    assert M.daily_tail_if_due(before, stopped) is None
