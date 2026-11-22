#!/usr/bin/env python3
"""Regression: empty risk.allowed_symbols must mean unrestricted, not deny-all."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from risk_limits import (  # noqa: E402
    RiskLimits,
    check_order,
    limits_from_config,
    normalize_allowed_symbols,
    resolve_halt_path,
)


def test_normalize_empty_means_unrestricted():
    assert normalize_allowed_symbols(None) is None
    assert normalize_allowed_symbols([]) is None
    assert normalize_allowed_symbols(()) is None
    assert normalize_allowed_symbols("") is None
    assert normalize_allowed_symbols(["", "  "]) is None
    assert normalize_allowed_symbols(["PUMP", "ada"]) == ("PUMP", "ADA")
    assert normalize_allowed_symbols("btc, eth") == ("BTC", "ETH")


def test_limits_from_config_empty_list_allows_any():
    # live_production.json / live_micro.json use [] and document it as "all".
    cfg = {"risk": {"allowed_symbols": [], "min_notional_usd": 5.0}}
    lim = limits_from_config(cfg)
    assert lim.allowed_symbols is None
    ok = check_order("PUMP-USD", "buy", 250.0, equity=1000.0, limits=lim)
    assert ok.approved, ok


def test_limits_from_config_missing_allows_any():
    lim = limits_from_config({"risk": {}})
    assert lim.allowed_symbols is None
    assert check_order("JASMY-USD", "buy", 250.0, equity=1000.0, limits=lim).approved


def test_explicit_allowlist_still_denies_others():
    cfg = {"risk": {"allowed_symbols": ["BTC", "ETH"]}}
    lim = limits_from_config(cfg)
    assert lim.allowed_symbols == ("BTC", "ETH")
    assert check_order("BTC-USD", "buy", 80.0, equity=1000.0, limits=lim).approved
    denied = check_order("PUMP-USD", "buy", 80.0, equity=1000.0, limits=lim)
    assert not denied.approved
    assert "allowlist" in " ".join(denied.reasons)


def test_check_order_empty_tuple_is_unrestricted():
    # Defense in depth: constructed empty tuple must not deny-all.
    lim = RiskLimits(allowed_symbols=())
    assert check_order("WLFI-USD", "buy", 80.0, equity=1000.0, limits=lim).approved


def test_mode_halt_isolation_and_global_panic():
    import tempfile
    import risk_limits as rl

    old_root = rl.ROOT
    try:
        with tempfile.TemporaryDirectory() as td:
            rl.ROOT = Path(td)
            (Path(td) / "data").mkdir()
            paper = RiskLimits(
                halt_mode="paper",
                halt_file=str(rl.halt_file_for_mode("paper")),
            )
            live = RiskLimits(
                halt_mode="live",
                halt_file=str(rl.halt_file_for_mode("live")),
            )
            assert rl.is_trading_halted(paper)[0] is False
            assert rl.is_trading_halted(live)[0] is False
            rl.halt_file_for_mode("live").write_text("stop\n")
            assert rl.is_trading_halted(live)[0] is True
            assert rl.is_trading_halted(paper)[0] is False
            rl.global_halt_path().write_text("panic\n")
            halted, why = rl.is_trading_halted(paper)
            assert halted is True
            assert "global" in why
    finally:
        rl.ROOT = old_root


def test_relative_halt_file_resolves_to_repo_root():
    p = resolve_halt_path("data/TRADING_HALTED_paper")
    assert p.is_absolute()
    assert p.name == "TRADING_HALTED_paper"
    assert "pro-trader" in str(p)


def test_micro_mtm_headroom_allows_fourth_clip():
    """2026-08-23: 3 live lots MTM ~$196 + leftover $62 clip denied at cap $250."""
    import tempfile

    mtm = 196.1675
    clip = 62.265876
    with tempfile.TemporaryDirectory() as td:
        tight = RiskLimits(
            max_position_notional_usd=62.5,
            max_total_notional_usd=250.0,
            max_alloc_pct=0.25,
            max_concurrent=8,
            min_notional_usd=5.0,
            halt_file=str(Path(td) / "nohalt"),
        )
        denied = check_order(
            "ZEC-USD",
            "buy",
            clip,
            current_total_notional=mtm,
            open_positions=3,
            equity=258.50,
            is_close=False,
            limits=tight,
        )
        assert not denied.approved, denied
        assert any("total notional" in r for r in denied.reasons)

        room = RiskLimits(
            max_position_notional_usd=62.5,
            max_total_notional_usd=312.5,
            max_alloc_pct=0.25,
            max_concurrent=8,
            min_notional_usd=5.0,
            halt_file=str(Path(td) / "nohalt"),
        )
        ok = check_order(
            "ZEC-USD",
            "buy",
            clip,
            current_total_notional=mtm,
            open_positions=3,
            equity=258.50,
            is_close=False,
            limits=room,
        )
        assert ok.approved, ok


def test_zero_dollar_caps_mean_percent_only():
    lim = RiskLimits(
        max_position_notional_usd=0.0,
        max_total_notional_usd=0.0,
        max_alloc_pct=0.25,
        daily_loss_halt_usd=0.0,
        daily_loss_halt_pct=0.10,
    )
    ok = check_order("ETH-USD", "buy", 250.0, equity=1000.0, limits=lim)
    assert ok.approved, ok
    denied = check_order("ETH-USD", "buy", 300.0, equity=1000.0, limits=lim)
    assert not denied.approved
    assert any("max_alloc_pct" in r for r in denied.reasons)
    halt = check_order(
        "ETH-USD",
        "buy",
        80.0,
        equity=900.0,
        daily_loss_usd=120.0,
        day_start_equity=1000.0,
        limits=lim,
    )
    assert not halt.approved
    assert any("daily loss" in r for r in halt.reasons)


def test_live_micro_is_percent_sized():
    from strategies.config_load import load_config

    cfg = load_config(ROOT / "config" / "live_micro.json")
    lim = limits_from_config(cfg)
    assert abs(float(cfg["start_usd"]) - 250.0) < 1e-9
    assert float(lim.max_position_notional_usd or 0) == 0.0
    assert float(lim.max_total_notional_usd or 0) == 0.0
    assert abs(lim.daily_loss_halt_pct - 0.10) < 1e-9
    assert abs(lim.max_alloc_pct - 0.25) < 1e-9


if __name__ == "__main__":
    test_normalize_empty_means_unrestricted()
    test_limits_from_config_empty_list_allows_any()
    test_limits_from_config_missing_allows_any()
    test_explicit_allowlist_still_denies_others()
    test_check_order_empty_tuple_is_unrestricted()
    test_mode_halt_isolation_and_global_panic()
    test_relative_halt_file_resolves_to_repo_root()
    test_micro_mtm_headroom_allows_fourth_clip()
    test_zero_dollar_caps_mean_percent_only()
    test_live_micro_is_percent_sized()
    print("ok risk_limits allowlist")
