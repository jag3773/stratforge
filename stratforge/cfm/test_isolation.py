#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfm import STRATEGY_FAMILY
from cfm.isolation import FORBIDDEN


def _walk_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.append(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    return names


def test_forbidden_modules_not_imported() -> None:
    root = Path(__file__).resolve().parent
    bad: list[str] = []
    for p in root.glob("*.py"):
        for name in _walk_imports(p):
            if name in FORBIDDEN:
                bad.append(f"{p.name}:{name}")
    assert not bad, bad


def test_package_exports_family() -> None:
    assert STRATEGY_FAMILY == "cfm_research"


if __name__ == "__main__":
    test_forbidden_modules_not_imported()
    test_package_exports_family()
    print("ok isolation")
