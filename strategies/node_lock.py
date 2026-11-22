"""Exclusive flock so two live_node processes cannot share a mode."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NodeLock:
    def __init__(self, mode: str, lock_dir: str | Path | None = None):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (mode or "paper"))
        base = Path(lock_dir) if lock_dir else ROOT / "data"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"live_node_{safe}.lock"
        self._fd = None

    def acquire(self) -> None:
        self._fd = open(self.path, "a+")
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self._fd.seek(0)
            holder = (self._fd.read() or "").strip() or "unknown"
            raise SystemExit(
                f"live_node already running for this mode (lock={self.path} holder={holder})"
            ) from e
        self._fd.seek(0)
        self._fd.truncate()
        self._fd.write(f"{os.getpid()}\n")
        self._fd.flush()

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fd.close()
        except Exception:
            pass
