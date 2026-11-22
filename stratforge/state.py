"""Where journals, configs, and the live-approval file live.

The package directory is not the book. STRATFORGE_STATE wins. Otherwise the
current directory, if it has config/. The approval file is still required.
This only chooses the directory. It cannot turn the gate off.
"""
from __future__ import annotations

import os
from pathlib import Path


def state_root() -> Path:
    env = os.environ.get("STRATFORGE_STATE")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "config").is_dir():
        return cwd
    return Path(__file__).resolve().parents[1]
