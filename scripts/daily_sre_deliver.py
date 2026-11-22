#!/usr/bin/env python3
"""Deliver daily SRE ping: one line if GREEN, full report otherwise."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("NAUTILUS_ROOT", Path(__file__).resolve().parents[1]))
REPORT = Path(os.environ.get("SRE_DAILY_REPORT", str(ROOT / "data" / "sre_daily_latest.md")))
MAX_AGE_SEC = float(os.environ.get("SRE_REPORT_MAX_AGE_SEC", 6 * 3600))
GREEN_LINE = os.environ.get("SRE_GREEN_LINE", "✅ SRE check clean.")


def _verdict_line(text: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if s:
            return s
    return ""


def _commit_reports() -> None:
    script = ROOT / "scripts" / "commit_sre_reports.sh"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [str(script)],
            cwd=str(ROOT),
            check=False,
            timeout=60,
        )
    except Exception:
        pass


def main() -> int:
    if not REPORT.is_file():
        print("**SRE daily RED**")
        print(f"missing report file: {REPORT}")
        print("Agent job did not write data/sre_daily_latest.md.")
        return 0
    text = REPORT.read_text()
    age = datetime.now(timezone.utc).timestamp() - REPORT.stat().st_mtime
    head = _verdict_line(text)
    green = head.upper().startswith("VERDICT: GREEN")
    if green and age <= MAX_AGE_SEC:
        print(GREEN_LINE)
        _commit_reports()
        return 0
    if green and age > MAX_AGE_SEC:
        print("**SRE daily YELLOW**")
        print(f"last GREEN report is stale age_h={age/3600:.1f} (file not refreshed)")
        print()
        print(text.rstrip())
        _commit_reports()
        return 0
    print(text.rstrip())
    _commit_reports()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
