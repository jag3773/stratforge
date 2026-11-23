"""Watchdog must see python -m stratforge.live, not only live_node.py."""
from __future__ import annotations

from pathlib import Path

from scripts.ops_watchdog import classify_live_node_lines, collect_watchdog_messages


PAPER = "/home/jarvis/pro-trader-system/.venv/bin/python -u -m stratforge.live --config config/live_paper.json"
LIVE = "/home/jarvis/pro-trader-system/.venv/bin/python -u -m stratforge.live --mode live --config config/live_micro.json"
DONCH = "/home/jarvis/pro-trader-system/.venv/bin/python -u -m stratforge.paper_family --config config/live_paper_donch.json"
PGREP = "pgrep -af stratforge.live|live_node.py"


def test_classifies_module_entry_not_script_path():
    got = classify_live_node_lines([PGREP, DONCH, PAPER, LIVE])
    assert got["paper"] == [PAPER]
    assert got["live"] == [LIVE]
    assert got["unexpected"] == []


def test_missing_live_pid_names_module_entry(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "LIVE_APPROVED").write_text("yes\n")
    (data / "feed_health.json").write_text('{"ts": "2099-01-01T00:00:00+00:00"}\n')
    msgs = collect_watchdog_messages(tmp_path, [PAPER, DONCH])
    assert "LIVE authorized but no stratforge.live PID" in msgs
    assert not any("live_node" in m for m in msgs)
