#!/usr/bin/env python3
"""Thin-host Phase 2+3: ccxt-only host, no LiveNode branches.

Never places real orders. Never creates LIVE_APPROVED.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_live_configs_backend_ccxt():
    for rel in ("config/live_micro.json", "config/live_production.json"):
        raw = json.loads((ROOT / rel).read_text())
        assert raw["execution_backend"] == "ccxt", rel


def test_load_backend_is_ccxt_only():
    import ccxt_bridge
    from strategies.live_executor import _load_backend

    for name in (None, "ccxt"):
        backend, module = _load_backend(name)
        assert backend == "ccxt"
        assert module is ccxt_bridge

    with pytest.raises(ValueError):
        _load_backend("native_v2")


def test_select_data_feed_always_rest(monkeypatch):
    from strategies import live_node as ln
    from strategies.live_data_feed import LiveDataFeed

    monkeypatch.setenv("NATIVE_LIVENODE_BARS", "1")
    for mode in ("paper", "live"):
        assert ln.select_data_feed_cls(mode) is LiveDataFeed


def test_live_node_has_no_strategy_adapter():
    from strategies import live_node as ln

    assert not hasattr(ln, "build_strategy_adapter")


def test_hot_path_does_not_import_nautilus():
    if "nautilus_trader" in sys.modules:
        exe = ROOT / ".venv" / "bin" / "python"
        script = (
            "import sys; "
            "from strategies import live_node, live_executor; "
            "import ccxt_bridge; "
            "from strategies import bar_apply; "
            'assert "nautilus_trader" not in sys.modules'
        )
        r = subprocess.run(
            [str(exe), "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            | {"PYTHONPATH": str(ROOT)},
        )
        assert r.returncode == 0, r.stderr or r.stdout
        return

    importlib.reload(importlib.import_module("strategies.live_node"))
    importlib.reload(importlib.import_module("strategies.live_executor"))
    importlib.reload(importlib.import_module("ccxt_bridge"))
    importlib.reload(importlib.import_module("strategies.bar_apply"))
    assert "nautilus_trader" not in sys.modules


def test_live_node_has_no_native_run_foreground():
    text = (ROOT / "stratforge" / "strategies" / "live_node.py").read_text()
    assert "native_exec_bridge" not in text
    assert "run_foreground" not in text


def test_archived_nautilus_costume_not_importable_from_repo_root():
    """Phase 4: Nautilus costume lives under archive/thin_host_nautilus, not import path."""
    for name in (
        "native_exec_bridge",
        "strategies.native_data_feed",
        "strategies.native_order_router",
        "strategies.v2_trend_rotation_strategy",
        "strategies.livenode_drive",
    ):
        with pytest.raises(ImportError):
            importlib.import_module(name)


def test_live_node_has_no_native_health_hooks():
    text = (ROOT / "stratforge" / "strategies" / "live_node.py").read_text()
    assert "native_livenode_bars" not in text
    assert "NATIVE_LIVENODE_BARS" not in text
    assert "build_strategy_adapter" not in text
    assert "v2_strategy" not in text


def test_ccxt_bridge_venue_symbol_not_nautilus_named():
    import ccxt_bridge

    text = (ROOT / "stratforge" / "ccxt_bridge.py").read_text()
    assert "to_nautilus_symbol" not in text
    assert ccxt_bridge.to_venue_symbol("BTC/USD") == "BTC-USD"
    assert ccxt_bridge.to_venue_symbol("eth-usd") == "ETH-USD"


def test_nautilus_catalog_helpers_removed():
    assert not (ROOT / "scripts" / "wrangle_to_nautilus.py").exists()
    assert not (ROOT / "scripts" / "wrangle_5m.py").exists()
    assert not (ROOT / "deploy" / "logrotate-nautilus").exists()
    assert not (ROOT / "deploy" / "logrotate-pro-trader").exists()
    assert not (ROOT / "deploy" / "hermes-cron-wrappers" / "rotate_logs.sh").exists()


def test_live_node_has_no_shadow_mode():
    text = (ROOT / "stratforge" / "strategies" / "live_node.py").read_text()
    assert "--mode shadow" not in text
    assert 'choices=["paper", "shadow", "live"]' not in text
    assert "execution_log_shadow" not in text


def test_build_executor_rejects_shadow():
    from strategies import live_node as ln
    from strategies.config_load import load_config

    cfg = load_config(ROOT / "config" / "live_paper.json")
    with pytest.raises(SystemExit):
        ln._build_executor("shadow", cfg)
