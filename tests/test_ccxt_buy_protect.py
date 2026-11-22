#!/usr/bin/env python3
"""ccxt limit IOC buy + L1 snapshot. Mocked client only — never hits Coinbase."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["SIMULATION_MODE"] = "true"
os.environ.pop("TRADING_HALTED", None)


class FakeClient:
    def __init__(self, ticker: dict | None = None, order: dict | None = None):
        self.ticker = ticker
        self.order = order
        self.create_order_calls: list[tuple] = []
        self.fetch_ticker_calls: list[str] = []

    def fetch_ticker(self, symbol: str) -> dict:
        self.fetch_ticker_calls.append(symbol)
        if self.ticker is None:
            return {}
        return self.ticker

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_order_calls.append((symbol, order_type, side, amount, price, params))
        if self.order is not None:
            return self.order
        px = float(price or 0)
        qty = float(amount)
        return {
            "id": "mock-oid",
            "status": "closed",
            "filled": qty,
            "average": px,
            "cost": qty * px,
            "fee": {"cost": 0.01, "currency": "USD"},
        }


def _live_cfg(journal_path: str) -> dict[str, Any]:
    return {
        "dry_run": False,
        "live_requested": True,
        "approval_ok": True,
        "approval_reason": "test-mock",
        "api_key": "k",
        "api_secret": "s",
        "use_cfm": False,
        "leverage": 1.0,
        "journal_path": journal_path,
        "long_only_default": True,
        "portfolio_id": "",
    }


def test_dry_run_limit_buy_ioc_journals_limit_price_no_create_order(tmp_path, monkeypatch):
    import ccxt_bridge

    client = FakeClient()
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)

    j = tmp_path / "bridge.jsonl"
    r = ccxt_bridge.limit_buy_ioc("BTC-USD", 0.001, 60000.0, journal_path=str(j))
    assert r["action"] == "DRY_RUN_ORDER"
    assert r["status"] == "SIMULATED"
    assert r["order_type"] == "limit"
    assert r["avg_price"] == 60000.0
    assert r["filled"] == 0.001
    assert r["side"] == "buy"
    assert client.create_order_calls == []
    lines = j.read_text().strip().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["action"] == "DRY_RUN_ORDER"
    assert logged["order_type"] == "limit"
    assert logged["avg_price"] == 60000.0
    assert logged.get("ref_price") == 60000.0


def test_live_mocked_create_order_limit_ioc(tmp_path, monkeypatch):
    import ccxt_bridge

    client = FakeClient()
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)

    j = tmp_path / "bridge.jsonl"
    cfg = _live_cfg(str(j))
    r = ccxt_bridge.limit_buy_ioc(
        "ETH-USD", 0.25, 3500.0, journal_path=str(j), cfg=cfg
    )
    assert r["action"] == "LIVE_ORDER"
    assert len(client.create_order_calls) == 1
    symbol, order_type, side, amount, price, params = client.create_order_calls[0]
    assert symbol == "ETH/USD"
    assert order_type == "limit"
    assert side == "buy"
    assert amount == 0.25
    assert price == 3500.0
    assert params["timeInForce"] == "IOC"
    assert "time_in_force" not in params


def test_limit_requires_positive_price_before_create_order(tmp_path, monkeypatch):
    import ccxt_bridge

    client = FakeClient()
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)
    j = tmp_path / "bridge.jsonl"
    cfg = _live_cfg(str(j))
    with pytest.raises(ValueError):
        ccxt_bridge.place_order(
            "BTC-USD",
            "buy",
            0.001,
            order_type="limit",
            price=None,
            journal_path=str(j),
            cfg=cfg,
        )
    with pytest.raises(ValueError):
        ccxt_bridge.place_order(
            "BTC-USD",
            "buy",
            0.001,
            order_type="limit",
            price=0,
            journal_path=str(j),
            cfg=cfg,
        )
    with pytest.raises(ValueError):
        ccxt_bridge.place_order(
            "BTC-USD",
            "buy",
            0.001,
            order_type="limit",
            price=float("nan"),
            journal_path=str(j),
            cfg=cfg,
        )
    assert client.create_order_calls == []


def test_fetch_l1_parses_bid_ask_mid(monkeypatch):
    import ccxt_bridge

    client = FakeClient(ticker={"bid": 99.5, "ask": 100.5})
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)
    out = ccxt_bridge.fetch_l1("BTC-USD")
    assert out["bid"] == 99.5
    assert out["ask"] == 100.5
    assert out["mid"] == 100.0
    assert client.fetch_ticker_calls == ["BTC/USD"]


def test_fetch_l1_raises_on_missing_bid_ask(monkeypatch):
    import ccxt_bridge

    client = FakeClient(ticker={"bid": None, "ask": 100.0})
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)
    with pytest.raises(ValueError):
        ccxt_bridge.fetch_l1("BTC-USD")

    client2 = FakeClient(ticker={"last": 100.0})
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client2)
    with pytest.raises(ValueError):
        ccxt_bridge.fetch_l1("ETH-USD")


def test_fetch_l1_does_not_journal(tmp_path, monkeypatch):
    import ccxt_bridge

    client = FakeClient(ticker={"bid": 1.0, "ask": 2.0})
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)
    j = tmp_path / "bridge.jsonl"
    ccxt_bridge.fetch_l1("BTC-USD", journal_path=str(j))
    assert not j.exists()


def test_market_buy_still_order_type_market(tmp_path, monkeypatch):
    import ccxt_bridge

    client = FakeClient()
    monkeypatch.setattr(ccxt_bridge, "get_client", lambda cfg=None: client)
    j = tmp_path / "bridge.jsonl"
    r = ccxt_bridge.market_buy("BTC-USD", 0.002, price=61000.0, journal_path=str(j))
    assert r["action"] == "DRY_RUN_ORDER"
    assert r["order_type"] == "market"
    assert client.create_order_calls == []

    cfg = _live_cfg(str(j))
    r2 = ccxt_bridge.market_buy(
        "BTC-USD", 0.002, price=61000.0, journal_path=str(j), cfg=cfg
    )
    assert r2["action"] == "LIVE_ORDER"
    assert len(client.create_order_calls) == 1
    _sym, order_type, side, _amt, _px, params = client.create_order_calls[0]
    assert order_type == "market"
    assert side == "buy"
    assert params.get("timeInForce") != "IOC"
    assert "timeInForce" not in params
