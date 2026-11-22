"""Pytest path bootstrap. Import the package so strategies resolves inside it."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import stratforge  # noqa: E402
