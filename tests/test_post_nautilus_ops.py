#!/usr/bin/env python3
"""Post-Nautilus operator contract.

Fail if docs/config/scripts still teach removed node modes or the old runtime.
Never enables LIVE.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OPERATOR_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "LIVE_READINESS.md",
    ROOT / "docs" / "PRODUCTION_BRIEF.md",
    ROOT / "docs" / "FRESH_VPS_SETUP.md",
    ROOT / "docs" / "PHASE0_V2_RUNTIME.md",
    ROOT / "docs" / "modules" / "deploy-and-ops.md",
    ROOT / "docs" / "modules" / "live-node.md",
    ROOT / "docs" / "diagrams" / "setup.md",
    ROOT / "docs" / "getting-started.md",
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


def test_env_example_is_ccxt_paper_or_live():
    text = (ROOT / ".env.example").read_text()
    assert "paper|live" in text
    assert "paper|shadow|live" not in text
    assert "EXECUTION_BACKEND=native_v2" not in text
    assert "EXECUTION_BACKEND=ccxt" in text


def test_paper_status_header_not_nautilus():
    text = (ROOT / "scripts" / "paper_status.py").read_text()
    assert "Nautilus Paper Trading Status" not in text
    assert "=== Paper Trading Status ===" in text


def test_live_node_docstring_modes_match_cli():
    text = (ROOT / "strategies" / "live_node.py").read_text()
    head = text.split('"""', 2)[1]
    assert "SHADOW" not in head
    assert "PAPER" in head and "LIVE" in head


def test_ci_runs_real_pytest_not_nautilus_excuse():
    text = (ROOT / ".github" / "workflows" / "ci-lite.yml").read_text()
    assert "pip install -r requirements.txt" in text
    assert "pytest tests/" in text
    assert "GitHub cannot run Nautilus nightly" not in text
