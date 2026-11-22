#!/usr/bin/env python3
"""Shim — tests live in tests/. Keeps python scripts/test_feed_emit_gate.py working."""
from __future__ import annotations

import runpy
from pathlib import Path

_target = Path(__file__).resolve().parents[1] / "tests" / "test_feed_emit_gate.py"
runpy.run_path(str(_target), run_name="__main__")
