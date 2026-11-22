#!/usr/bin/env python3
"""P1 ops: singleton lock, stale-bar gate, dotenv not auto-loaded."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"


def test_node_lock_exclusive():
    from strategies.node_lock import NodeLock

    with tempfile.TemporaryDirectory() as td:
        a = NodeLock("paper", lock_dir=td)
        b = NodeLock("paper", lock_dir=td)
        a.acquire()
        try:
            b.acquire()
            raise AssertionError("second lock should fail")
        except SystemExit as e:
            assert "already running" in str(e)
        a.release()
        b.acquire()
        b.release()
    print("ok node lock")


def test_bar_freshness():
    from strategies.bar_freshness import bar_tradable

    now = time.time()
    fresh_ts = int((now - 4 * 3600) * 1e9)
    ok, why = bar_tradable({"ts": fresh_ts}, now=now)
    assert ok, why
    stale_ts = int((now - 10 * 3600) * 1e9)
    ok, why = bar_tradable({"ts": stale_ts}, now=now)
    assert not ok and "stale" in why, why
    future_ts = int((now + 3600) * 1e9)
    ok, why = bar_tradable({"ts": future_ts}, now=now)
    assert not ok and "future" in why, why
    print("ok bar freshness")


def test_ccxt_does_not_autoload_env_keys():
    # Import already happened in other tests; check helper is opt-in.
    import ccxt_bridge

    assert not hasattr(ccxt_bridge, "_AUTOLOADED") or ccxt_bridge._AUTOLOADED is False
    # load_live_secrets exists and is callable
    assert callable(getattr(ccxt_bridge, "load_live_secrets", None))
    print("ok dotenv opt-in")


def test_normalize_pem_secret():
    import ccxt_bridge

    raw = "-----BEGIN EC PRIVATE KEY-----\\nQUJD\\nQUJD\\n-----END EC PRIVATE KEY-----\\n"
    got = ccxt_bridge.normalize_pem_secret(raw)
    assert "\\n" not in got
    assert got.count("\n") == 3
    already = "-----BEGIN EC PRIVATE KEY-----\nQUJD\nQUJD\n-----END EC PRIVATE KEY-----\n"
    assert ccxt_bridge.normalize_pem_secret(already) == already.strip()
    prev = os.environ.get("COINBASE_API_SECRET")
    os.environ["COINBASE_API_SECRET"] = raw
    try:
        cfg = ccxt_bridge.bridge_config()
        assert cfg["api_secret"] == got
        assert "\n" in cfg["api_secret"]
    finally:
        if prev is None:
            os.environ.pop("COINBASE_API_SECRET", None)
        else:
            os.environ["COINBASE_API_SECRET"] = prev
    print("ok pem unescape")


def test_ccxt_pins_retail_portfolio():
    import ccxt_bridge

    prev = os.environ.get("COINBASE_PORTFOLIO_ID")
    os.environ["COINBASE_PORTFOLIO_ID"] = "00000000-0000-4000-8000-000000000001"
    try:
        p = ccxt_bridge.live_order_params("buy-BTC-test")
        assert p["clientOrderId"] == "buy-BTC-test"
        assert p["retail_portfolio_id"] == "00000000-0000-4000-8000-000000000001"
    finally:
        if prev is None:
            os.environ.pop("COINBASE_PORTFOLIO_ID", None)
        else:
            os.environ["COINBASE_PORTFOLIO_ID"] = prev
    print("ok ccxt pins portfolio")


def test_preflight_bars_seen_zero_is_not_missing():
    from scripts.live_preflight import next_4h_close_unix, node_health_issues

    started = 1_776_240_000.0  # 2026-04-14 00:00 UTC-ish; exact value unused except math
    # just after start, still in same 4h bucket
    now = started + 60
    nh = {
        "bars_seen": 0,
        "status": "starting",
        "cash": 250.0,
        "equity": 250.0,
        "started_at": started,
    }
    assert node_health_issues(nh, now=now) == []
    # after next 4h close + 20m slack
    late = next_4h_close_unix(started) + 21 * 60
    issues = node_health_issues(nh, now=late)
    assert any("starting" in x or "bars_seen=0" in x for x in issues), issues
    missing = node_health_issues({"bars_seen": 0, "status": "starting"}, now=now)
    assert any("equity" in x for x in missing)
    # 0 must not be treated as missing
    ok_zero = node_health_issues(
        {"bars_seen": 0, "equity": 250.0, "status": "running", "started_at": started},
        now=now,
    )
    assert ok_zero == []
    print("ok preflight bars_seen")


if __name__ == "__main__":
    test_node_lock_exclusive()
    test_bar_freshness()
    test_ccxt_does_not_autoload_env_keys()
    test_normalize_pem_secret()
    test_ccxt_pins_retail_portfolio()
    test_preflight_bars_seen_zero_is_not_missing()
    print("ALL P1 OPS PASS")
