#!/usr/bin/env python3
"""Stdlib tests: `_include` DNA inherit. Safe on GitHub without Nautilus."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.config_load import (  # noqa: E402
    DNA_INCLUDE,
    DNA_KEYS,
    INCLUDE_KEY,
    copied_dna_keys,
    load_config,
)


def test_include_merges_then_overlay(tmp_path: Path):
    parent = tmp_path / "dna.json"
    child = tmp_path / "live.json"
    parent.write_text(json.dumps({"mode": "breakout", "max_concurrent": 8, "start_usd": 1000}))
    child.write_text(
        json.dumps(
            {
                INCLUDE_KEY: "dna.json",
                "start_usd": 250,
                "execution_backend": "native_v2",
            }
        )
    )
    cfg = load_config(child, root=tmp_path)
    assert cfg["mode"] == "breakout"
    assert cfg["max_concurrent"] == 8
    assert cfg["start_usd"] == 250
    assert cfg["execution_backend"] == "native_v2"
    assert INCLUDE_KEY not in cfg


def test_parent_key_is_not_followed(tmp_path: Path):
    base = tmp_path / "base.json"
    child = tmp_path / "child.json"
    base.write_text(json.dumps({"max_concurrent": 50, "rotate_when_full": False}))
    child.write_text(
        json.dumps(
            {
                "_parent": "base.json",
                "max_concurrent": 8,
                "rotate_when_full": True,
            }
        )
    )
    cfg = load_config(child, root=tmp_path)
    assert cfg["max_concurrent"] == 8
    assert cfg["_parent"] == "base.json"


def test_include_cycle_raises(tmp_path: Path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({INCLUDE_KEY: "b.json"}))
    b.write_text(json.dumps({INCLUDE_KEY: "a.json"}))
    try:
        load_config(a, root=tmp_path)
        raise AssertionError("expected cycle error")
    except ValueError as e:
        assert "cycle" in str(e)


def test_live_files_include_ssot_and_resolve():
    for rel in (
        "config/live_paper.json",
        "config/live_production.json",
        "config/live_micro.json",
    ):
        raw = json.loads((ROOT / rel).read_text())
        assert raw.get(INCLUDE_KEY) == DNA_INCLUDE
        assert copied_dna_keys(raw) == []
        cfg = load_config(ROOT / rel)
        assert cfg["mode"] == "breakout"
        assert cfg["max_concurrent"] == 8
        assert cfg["max_alloc_pct"] == 0.25
        assert bool(cfg["rotate_when_full"]) is True
        assert cfg["rotate_min_hold_bars"] == 14
        assert cfg["account_type"] == "CASH"
        for k in DNA_KEYS:
            assert k in cfg, (rel, k)


def test_validate_dna_green():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_dna.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr or r.stdout


if __name__ == "__main__":
    import tempfile

    test_include_merges_then_overlay(Path(tempfile.mkdtemp()))
    print("ok merge")
    test_parent_key_is_not_followed(Path(tempfile.mkdtemp()))
    print("ok parent ignored")
    test_include_cycle_raises(Path(tempfile.mkdtemp()))
    print("ok cycle")
    test_live_files_include_ssot_and_resolve()
    print("ok live include")
    test_validate_dna_green()
    print("ok validate")
    print("ALL CONFIG_LOAD PASS")
