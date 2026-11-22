"""Process entry. Import the package before the node so state and imports resolve."""
from __future__ import annotations

import runpy
import sys


def main() -> None:
    import stratforge  # noqa: F401
    sys.argv[0] = "live_node.py"
    runpy.run_module("strategies.live_node", run_name="__main__")


if __name__ == "__main__":
    main()
