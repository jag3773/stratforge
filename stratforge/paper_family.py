"""Donchian paper entry. Same state-root rule as the spot node."""
from __future__ import annotations

import runpy
import sys


def main() -> None:
    import stratforge  # noqa: F401
    sys.argv[0] = "paper_family_node.py"
    runpy.run_module("strategies.paper_family_node", run_name="__main__")


if __name__ == "__main__":
    main()
