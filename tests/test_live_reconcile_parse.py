"""Coinbase portfolio-detail parse (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.live_reconcile import overlay_account_available, parse_portfolio_detail


def _operator_usd_payload(amount: float = 250.0) -> dict:
    return {
        "breakdown": {
            "portfolio": {
                "name": "the live sleeve",
                "uuid": "00000000-0000-4000-8000-000000000001",
                "type": "CONSUMER",
            },
            "portfolio_balances": {
                "total_balance": {"value": str(amount), "currency": "USD"},
                "total_cash_equivalent_balance": {"value": str(amount), "currency": "USD"},
                "total_crypto_balance": {"value": "0", "currency": "USD"},
            },
            "spot_positions": [
                {
                    "asset": "USD",
                    "is_cash": True,
                    "total_balance_fiat": amount,
                    "total_balance_crypto": amount,
                    "available_to_trade_fiat": amount,
                    "available_to_trade_crypto": amount,
                    "account_type": "ACCOUNT_TYPE_FIAT",
                }
            ],
            "perp_positions": [],
            "futures_positions": [],
        }
    }


def test_parse_dict_breakdown_usd_cash():
    book = parse_portfolio_detail(_operator_usd_payload(250.0))
    assert book["cash_usd"] == 250.0
    assert book["portfolio_total_usd"] == 250.0
    assert book["positions"] == {}
    print("ok dict breakdown usd cash")


def test_parse_crypto_spot_and_cash():
    payload = _operator_usd_payload(100.0)
    payload["breakdown"]["spot_positions"].append(
        {
            "asset": "ETH",
            "is_cash": False,
            "total_balance_fiat": 50.0,
            "total_balance_crypto": 0.02,
            "available_to_trade_fiat": 50.0,
            "available_to_trade_crypto": 0.02,
        }
    )
    book = parse_portfolio_detail(payload)
    assert book["cash_usd"] == 100.0
    assert book["portfolio_total_usd"] == 150.0
    assert book["positions"]["ETH"] == 0.02
    print("ok crypto + cash")


def test_parse_legacy_list_breakdown():
    book = parse_portfolio_detail(
        {
            "breakdown": [
                {"currency": "USD", "available": 80.0, "value": 80.0},
                {"currency": "BTC", "available": 0.001, "value": 70.0},
            ]
        }
    )
    assert book["cash_usd"] == 80.0
    assert book["positions"]["BTC"] == 0.001
    assert book["portfolio_total_usd"] == 150.0
    print("ok legacy list breakdown")


def test_empty_payload_is_zero():
    book = parse_portfolio_detail({"breakdown": {"spot_positions": []}})
    assert book["cash_usd"] == 0.0
    assert book["portfolio_total_usd"] == 0.0
    assert book["positions"] == {}
    print("ok empty")


JARVIS = "00000000-0000-4000-8000-000000000001"


def test_overlay_uses_wallet_available_not_rounded_portfolio():
    positions = {"XLM": 1620.9435}
    accounts = [
        {
            "currency": "XLM",
            "available_balance": {"value": "1620.94349082", "currency": "XLM"},
            "hold": {"value": "0", "currency": "XLM"},
            "retail_portfolio_id": JARVIS,
        }
    ]
    out = overlay_account_available(positions, accounts, portfolio_id=JARVIS)
    assert out["XLM"] == float("1620.94349082")
    assert out["XLM"] < 1620.9435
    print("ok overlay wallet available")


def test_overlay_skips_other_portfolio_accounts():
    positions = {"XLM": 1620.9435}
    accounts = [
        {
            "currency": "XLM",
            "available_balance": {"value": "99.0", "currency": "XLM"},
            "retail_portfolio_id": "not-operator",
        }
    ]
    out = overlay_account_available(positions, accounts, portfolio_id=JARVIS)
    assert out["XLM"] == 1620.9435
    print("ok overlay other portfolio skipped")


if __name__ == "__main__":
    test_parse_dict_breakdown_usd_cash()
    test_parse_crypto_spot_and_cash()
    test_parse_legacy_list_breakdown()
    test_empty_payload_is_zero()
    test_overlay_uses_wallet_available_not_rounded_portfolio()
    test_overlay_skips_other_portfolio_accounts()
    print("ALL PASS")
