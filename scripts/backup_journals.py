#!/usr/bin/env python3
"""Copy execution journals to data/journal_backups/ (dated). Keep last KEEP copies."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("NAUTILUS_ROOT", Path(__file__).resolve().parents[1]))
SRC = [
    ROOT / "data" / "execution_log.jsonl",
    ROOT / "data" / "execution_log_live.jsonl",
]
DEST = ROOT / "data" / "journal_backups"
KEEP = int(os.environ.get("JOURNAL_BACKUP_KEEP", "14"))


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copied = 0
    for src in SRC:
        if not src.is_file():
            continue
        out = DEST / f"{src.name}.{stamp}"
        shutil.copy2(src, out)
        copied += 1
        if os.environ.get("VERBOSE") == "1":
            print(f"copied {src.name} -> {out.name}")
    # prune
    for prefix in ("execution_log.jsonl.", "execution_log_live.jsonl."):
        files = sorted(DEST.glob(prefix + "*"), key=lambda p: p.name, reverse=True)
        for old in files[KEEP:]:
            old.unlink(missing_ok=True)
    return 0 if copied else 0


if __name__ == "__main__":
    raise SystemExit(main())
