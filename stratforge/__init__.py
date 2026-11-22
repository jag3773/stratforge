"""stratforge public package.

Importing this module puts the package directory first on sys.path so the
existing ``strategies`` and ``ccxt_bridge`` imports resolve inside the
package, not a vendored tree on the process cwd.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG = str(Path(__file__).resolve().parent)
if _PKG in sys.path:
    sys.path.remove(_PKG)
sys.path.insert(0, _PKG)

__version__ = "0.1.0"
