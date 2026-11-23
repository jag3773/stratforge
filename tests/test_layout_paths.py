"""Package layout: state paths and empty short-regime input."""
from __future__ import annotations

from pathlib import Path

import pytest

from stratforge.cfm.venue import load_venue
from stratforge.short.regime_occupancy import build
from stratforge.state import state_root


def test_load_venue_uses_state_root(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "config" / "cfm"
    cfg.mkdir(parents=True)
    (cfg / "venue.json").write_text(
        '{"venue": "CFM", "contracts": {"BTC": {"multiplier": 0.01}}, "_comment": "x"}\n'
    )
    monkeypatch.setenv("STRATFORGE_STATE", str(tmp_path))
    venue = load_venue()
    assert venue["venue"] == "CFM"
    assert state_root() == tmp_path.resolve()


def test_regime_occupancy_empty_bars(monkeypatch):
    monkeypatch.setattr(
        "stratforge.short.regime_occupancy.load_4h_bars",
        lambda *a, **k: [],
    )
    df = build()
    assert df.empty
