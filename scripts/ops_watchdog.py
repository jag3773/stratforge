#!/usr/bin/env python3
"""Ops watchdog: duplicate PIDs, missing LIVE PID, halt file, feed stall.

Empty stdout if OK (Hermes silent). Non-zero + text pages Telegram.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("NAUTILUS_ROOT", Path(__file__).resolve().parents[1]))
FEED_MAX_AGE_SEC = float(os.environ.get("HEALTH_MAX_AGE_SEC", 20 * 60))
DONCH_DAILY_CLOSE_MAX_AGE_SEC = 26 * 3600


def _age(iso: str | None, *, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return (now - dt).total_seconds()
    except Exception:
        return None


def classify_live_node_lines(lines: list[str]) -> dict[str, list[str]]:
    paper: list[str] = []
    live: list[str] = []
    unexpected: list[str] = []
    for ln in lines:
        if "live_node.py" not in ln:
            continue
        if "pgrep" in ln or "hermes-snap" in ln:
            continue
        if "--mode shadow" in ln:
            unexpected.append(ln)
        elif "--mode live" in ln or "live_micro.json" in ln or "live_production.json" in ln:
            live.append(ln)
        else:
            paper.append(ln)
    return {"paper": paper, "live": live, "unexpected": unexpected}


def _feed_msgs(root: Path, rel: str, *, require: bool, now: datetime | None) -> list[str]:
    feed = root / "data" / rel
    if not feed.is_file():
        return [f"missing {rel}"] if require else []
    try:
        fh = json.loads(feed.read_text())
        age = _age(fh.get("ts"), now=now)
        if age is not None and age > FEED_MAX_AGE_SEC:
            return [f"{rel} stale age_sec={age:.0f}"]
    except Exception as e:
        return [f"{rel} unreadable: {e}"]
    return []


def collect_watchdog_messages(
    root: Path,
    proc_lines: list[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    msgs: list[str] = []
    classified = classify_live_node_lines(proc_lines)
    if len(classified["paper"]) > 1:
        msgs.append(f"duplicate paper live_node PIDs={len(classified['paper'])}")
    if classified["unexpected"]:
        msgs.append(f"unexpected shadow live_node PIDs={len(classified['unexpected'])}")
    if len(classified["live"]) > 1:
        msgs.append(f"duplicate LIVE live_node PIDs={len(classified['live'])}")

    approved = (root / "data" / "LIVE_APPROVED").is_file()
    if approved and len(classified["live"]) == 0:
        msgs.append("LIVE authorized but no live_node PID")

    halt_msgs = []
    for name in (
        "TRADING_HALTED",
        "TRADING_HALTED_paper",
        "TRADING_HALTED_live",
    ):
        if (root / "data" / name).is_file():
            halt_msgs.append(name)
    if halt_msgs:
        msgs.append("halt file(s): " + ", ".join(halt_msgs))

    msgs.extend(_feed_msgs(root, "feed_health.json", require=True, now=now))
    msgs.extend(_feed_msgs(root, "feed_health_live.json", require=approved, now=now))
    msgs.extend(_donch_daily_close_msgs(root, now=now))
    return msgs


_DAY_MS = 24 * 3600 * 1000


def _forming_utc_day_still_open(data: dict, now: datetime) -> bool:
    """True when every symbol is still folding the same unclosed UTC day."""
    forming = data.get("forming_buckets")
    if not isinstance(forming, dict) or not forming:
        return False
    try:
        buckets = {int(v) for v in forming.values()}
    except (TypeError, ValueError):
        return False
    if len(buckets) != 1:
        return False
    now_ms = int(now.timestamp() * 1000)
    return now_ms < next(iter(buckets)) + _DAY_MS


def _donch_daily_close_msgs(root: Path, now: datetime | None = None) -> list[str]:
    """Page when the donch soak missed a UTC daily close. PID age does not count.

    A same-process stall keeps rewriting feed `ts` on every poll. The close
    clock is `last_bar_ts`. File absent means the soak is not in this check.
    A null stamp after a mid-day restart is not a miss while `forming_buckets`
    are still that open UTC day — the closed day was seed-deduped, not skipped.
    A closed forming bucket, mixed buckets, or no forming map still pages.
    """
    path = root / "data" / "feed_health_donch.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return [f"feed_health_donch.json unreadable ({e})"]
    now = now or datetime.now(timezone.utc)
    stamp = data.get("last_bar_ts")
    age = _age(str(stamp) if stamp else None, now=now)
    if age is None:
        if _forming_utc_day_still_open(data, now):
            return []
        return ["donch daily close missing age_sec=none"]
    if age > DONCH_DAILY_CLOSE_MAX_AGE_SEC:
        return [f"donch daily close missing age_sec={age:.0f}"]
    return []


def main() -> int:
    try:
        r = subprocess.run(["pgrep", "-af", "live_node.py"], capture_output=True, text=True)
        proc_lines = (r.stdout or "").splitlines()
    except Exception as e:
        print("OPS WATCHDOG")
        print(f"- pgrep failed: {e}")
        return 1

    msgs = collect_watchdog_messages(ROOT, proc_lines)
    if msgs:
        print("OPS WATCHDOG")
        for m in msgs:
            print(f"- {m}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
