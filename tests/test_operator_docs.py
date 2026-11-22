#!/usr/bin/env python3
"""Operator docs match the package entry point.

Fail if docs still teach a removed node mode.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OPERATOR_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "getting-started.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "PROMOTE.md",
    ROOT / "docs" / "DATA_AND_PERSISTENCE.md",
)


def test_no_shadow_unit_or_parity_script():
    assert not (ROOT / "deploy" / "pro-trader-shadow.service").exists()
    assert not (ROOT / "scripts" / "shadow_paper_parity.py").exists()
    assert not (ROOT / "requirements-v2.txt").exists()


def test_operator_docs_do_not_teach_removed_shadow_node():
    forbidden = (
        "--mode shadow",
        "pro-trader-shadow.service",
        "scripts/shadow_paper_parity.py",
    )
    hits: list[str] = []
    for path in OPERATOR_DOCS:
        text = path.read_text()
        for needle in forbidden:
            if needle in text:
                hits.append(f"{path.relative_to(ROOT)}: {needle}")
    assert hits == [], "operator docs still teach removed shadow node:\n" + "\n".join(hits)


def test_operator_docs_do_not_claim_venv_v2_alias():
    hits: list[str] = []
    for path in OPERATOR_DOCS:
        text = path.read_text()
        if "requirements-v2.txt" in text:
            hits.append(str(path.relative_to(ROOT)))
    assert hits == [], "docs claim missing requirements-v2.txt: " + ", ".join(hits)


def test_env_example_stays_paper():
    text = (ROOT / ".env.example").read_text()
    assert "LIVE_TRADING=false" in text
    assert "SIMULATION_MODE=true" in text
    assert "shadow" not in text


def test_paper_status_header():
    text = (ROOT / "scripts" / "paper_status.py").read_text()
    assert "=== Paper Trading Status ===" in text


def test_live_node_docstring_modes_match_cli():
    text = (ROOT / "stratforge" / "strategies" / "live_node.py").read_text()
    head = text.split('"""', 2)[1]
    assert "SHADOW" not in head
    assert "PAPER" in head and "LIVE" in head


def test_publish_workflow_has_no_token():
    text = (ROOT / ".github" / "workflows" / "workflow.yml").read_text()
    assert "password" not in text.lower()
    assert "PYPI_TOKEN" not in text
