#!/usr/bin/env python3
"""Live readiness preflight.

Checks ops health, strategy DNA, risk gates, and bridge flags.
Never enables LIVE. Never creates data/LIVE_APPROVED.

Usage:
  python scripts/live_preflight.py              # paper ops + phase2 readiness
  python scripts/live_preflight.py --mode live  # reports what's still blocking LIVE
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROD_DNA = {
    "strategy_id": "trend_rotation_25_max8_rotate",
    "mode": "breakout",
    "channel_period": 60,
    "exit_mode": "slow_ema",
    "atr_stop_mult": 0.0,
    "max_alloc_pct": 0.25,
    "max_concurrent": 8,
    "rotate_when_full": True,
    "rotate_min_hold_bars": 14,
}


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _bad(msg: str) -> None:
    print(f"  BAD {msg}")


def _warn(msg: str) -> None:
    print(f" WARN {msg}")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def check_dna(cfg_path: Path) -> list[str]:
    from strategies.config_load import load_config

    errs = []
    cfg = load_config(cfg_path)
    for k, v in PROD_DNA.items():
        got = cfg.get(k)
        if k == "rotate_when_full":
            got = bool(got)
        if got != v:
            errs.append(f"{cfg_path.name}: {k}={got!r} expected {v!r}")
    risk = cfg.get("risk") or {}
    if int(risk.get("max_concurrent", cfg.get("max_concurrent", 0))) != 8:
        errs.append(f"{cfg_path.name}: risk.max_concurrent must be 8")
    if not bool(cfg.get("long_only", True)):
        errs.append(f"{cfg_path.name}: long_only must be true")
    if str(cfg.get("account_type", "CASH")).upper() != "CASH":
        errs.append(f"{cfg_path.name}: account_type must be CASH")
    return errs


def _first_present(payload: dict, *keys, default=None):
    """Return the first key that is present (0 is a valid value)."""
    for k in keys:
        if k in payload and payload[k] is not None:
            return payload[k]
    return default


def next_4h_close_unix(started_ts: float) -> float:
    period = 4 * 3600
    return float((int(started_ts) // period + 1) * period)


def _parse_iso(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        return v / 1e9 if v > 1e12 else v
    if isinstance(ts, str):
        from datetime import datetime, timezone

        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None
    return None


def node_health_issues(
    nh: dict,
    *,
    now: float | None = None,
    max_start_lag_sec: float = 20 * 60,
) -> list[str]:
    """Validate node_health payload. bars_seen=0 is not missing."""
    import time

    now_s = time.time() if now is None else float(now)
    errs: list[str] = []
    bars = _first_present(nh, "bars_seen", "bars", "bar_count")
    equity = _first_present(nh, "equity")
    cash = _first_present(nh, "cash")
    if equity is None and cash is None:
        errs.append("node_health missing equity and cash")
    status = str(nh.get("status") or "")
    if bars is None:
        errs.append("node_health missing bars_seen")
        return errs
    try:
        bars_n = int(bars)
    except (TypeError, ValueError):
        errs.append(f"node_health bars_seen unparsed: {bars!r}")
        return errs
    if bars_n == 0 or status == "starting":
        started = _parse_iso(nh.get("started_at") or nh.get("ts"))
        if started is not None:
            deadline = next_4h_close_unix(started) + max_start_lag_sec
            if now_s > deadline:
                errs.append(
                    f"node still starting/bars_seen=0 after 4h close "
                    f"(started={started:.0f} deadline={deadline:.0f})"
                )
    return errs


def check_health(mode: str = "paper") -> list[str]:
    errs = []
    suffix = "" if mode == "paper" else f"_{mode}"
    feed = ROOT / "data" / f"feed_health{suffix}.json"
    node = ROOT / "data" / f"node_health{suffix}.json"
    now = time.time()
    if not feed.is_file():
        errs.append(f"missing {feed.name}")
    else:
        fh = _load_json(feed)
        ts = fh.get("ts") or fh.get("timestamp")
        age = _parse_iso(ts)
        age = None if age is None else now - age
        if age is None:
            _warn(f"{feed.name} present but ts unparsed: {ts!r}")
        elif age > 1200:
            errs.append(f"{feed.name} stale age_sec={age:.0f}")
        else:
            _ok(f"{feed.name} age_sec={age:.0f} ok={fh.get('fetch_ok') or fh.get('ok')}")

    if not node.is_file():
        errs.append(f"missing {node.name}")
    else:
        nh = _load_json(node)
        bars = _first_present(nh, "bars_seen", "bars", "bar_count")
        eq = _first_present(nh, "equity")
        _ok(f"{node.name} bars_seen={bars} equity={eq} status={nh.get('status')}")
        issues = node_health_issues(nh, now=now)
        if issues:
            errs.extend(issues)
            for x in issues:
                _bad(x)
    return errs


def check_paper_service() -> list[str]:
    errs = []
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "pro-trader-paper.service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        state = (r.stdout or r.stderr or "").strip()
        if state != "active":
            errs.append(f"pro-trader-paper.service state={state!r}")
        else:
            _ok("pro-trader-paper.service active")
    except Exception as e:
        errs.append(f"systemctl check failed: {e}")

    try:
        r = subprocess.run(["pgrep", "-af", "live_node.py"], capture_output=True, text=True)
        lines = [ln for ln in (r.stdout or "").splitlines() if "live_node.py" in ln and "pgrep" not in ln]
        # filter shadow/live if any
        paper = [ln for ln in lines if "live_paper" in ln or "live_paper.json" in ln or "--mode" not in ln]
        if len(lines) == 0:
            errs.append("no live_node.py process")
        elif len(lines) > 2:
            _warn(f"multiple live_node PIDs ({len(lines)}) — check orphans")
        else:
            _ok(f"live_node PIDs={len(lines)}")
    except Exception as e:
        errs.append(f"pgrep failed: {e}")
    return errs


def _production_python() -> Path | None:
    """Sole runtime: .venv (ccxt/pandas; Nautilus archived)."""
    p = ROOT / ".venv" / "bin" / "python"
    return p if p.is_file() else None


def run_tests() -> list[str]:
    errs = []
    py = _production_python()
    if py is None:
        return ["missing .venv/bin/python — create with requirements.txt"]
    _ok(f"test interpreter {py.relative_to(ROOT)}")
    pytest_files = (
        "tests/test_thin_host_cutover.py",
        "tests/test_phase2_execution.py",
        "tests/test_p0_live_safety.py",
    )
    r = subprocess.run(
        [str(py), "-m", "pytest", *pytest_files, "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "LIVE_TRADING": "false", "SIMULATION_MODE": "true"},
    )
    if r.returncode != 0:
        errs.append(
            f"pytest {' '.join(pytest_files)} failed rc={r.returncode}: "
            f"{(r.stdout or r.stderr)[-300:]}"
        )
    else:
        _ok(" ".join(pytest_files))
    for script in (
        "scripts/test_paper_executor.py",
        "scripts/test_signal_parity.py",
        "scripts/test_live_reconcile_parse.py",
        "scripts/test_p1_ops.py",
        "scripts/test_chaos_journal.py",
        "scripts/test_admission.py",
    ):
        r = subprocess.run(
            [str(py), str(ROOT / script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "LIVE_TRADING": "false", "SIMULATION_MODE": "true"},
        )
        if r.returncode != 0:
            errs.append(f"{script} failed rc={r.returncode}: {(r.stdout or r.stderr)[-300:]}")
        else:
            _ok(script)
    return errs


def check_bridge(mode: str) -> list[str]:
    errs = []
    import ccxt_bridge

    if mode == "live":
        ccxt_bridge.load_live_secrets()
    st = ccxt_bridge.status()
    _ok(f"ccxt dry_run={st['dry_run']} has_keys={st['has_keys']} live_requested={st.get('live_requested')}")

    approval = ROOT / "data" / "LIVE_APPROVED"
    from risk_limits import global_halt_path, halt_file_for_mode, is_trading_halted

    halted, why = is_trading_halted(mode=mode)
    global_halt = global_halt_path()
    mode_halt = halt_file_for_mode(mode)
    if halted or global_halt.is_file() or mode_halt.is_file():
        errs.append(f"kill switch active ({why or mode_halt.name})")
    else:
        _ok("kill switch not engaged")

    if mode == "live":
        if os.getenv("LIVE_TRADING", "false").lower() != "true":
            errs.append("LIVE_TRADING is not true")
        if os.getenv("SIMULATION_MODE", "true").lower() != "false":
            errs.append("SIMULATION_MODE is not false")
        if not approval.is_file():
            errs.append("missing data/LIVE_APPROVED (create only after explicit in-session approval)")
        if not st["has_keys"]:
            errs.append("COINBASE_API_KEY/SECRET not set in environment")
        if st["dry_run"]:
            errs.append("bridge still dry_run=True — LIVE would be refused")
        pid = (os.getenv("COINBASE_PORTFOLIO_ID") or "").strip()
        if not pid:
            errs.append("COINBASE_PORTFOLIO_ID unset — LIVE has no silent default")
        else:
            _ok(f"COINBASE_PORTFOLIO_ID set ({pid[:8]}…)")
            try:
                from strategies.live_reconcile import fetch_coinbase_book

                book = fetch_coinbase_book(pid)
                if not book.get("ok"):
                    errs.append(f"portfolio fetch failed: {book.get('error')}")
                else:
                    total = float(book.get("portfolio_total_usd") or 0.0)
                    cash = float(book.get("cash_usd") or 0.0)
                    _ok(f"portfolio fetch ok cash≈{cash:.2f} total≈{total:.2f}")
                    if total <= 0.01 and cash <= 0.01:
                        errs.append(
                            "configured portfolio breakdown is ~$0 — fund the sleeve before LIVE"
                        )
            except Exception as e:
                errs.append(f"portfolio check error: {e}")
    else:
        if approval.is_file():
            _warn("data/LIVE_APPROVED exists while not in live mode")
        if not st["dry_run"]:
            errs.append("bridge dry_run=False unexpectedly outside live intent")
    return errs


def check_v2_optional() -> list[str]:
    """Nautilus adapter verify is archived. Thin-host tests live in run_tests()."""
    _ok("Nautilus verify skipped (archive/thin_host_nautilus)")
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()

    print(f"=== live_preflight mode={args.mode} ===")
    print(f"root={ROOT}")
    all_errs: list[str] = []

    print("\n[DNA]")
    for rel in (
        "config/trend_rotation_25_max8_rotate.json",
        "config/live_paper.json",
        "config/live_production.json",
        "config/live_micro.json",
    ):
        p = ROOT / rel
        if not p.is_file():
            all_errs.append(f"missing {rel}")
            _bad(f"missing {rel}")
            continue
        e = check_dna(p)
        if e:
            all_errs.extend(e)
            for x in e:
                _bad(x)
        else:
            _ok(rel)

    print("\n[ops]")
    all_errs.extend(check_paper_service())
    all_errs.extend(check_health(args.mode))

    print("\n[bridge/safety]")
    all_errs.extend(check_bridge(args.mode))

    if not args.skip_tests:
        print("\n[tests]")
        all_errs.extend(run_tests())
    else:
        print("\n[tests] skipped")

    print("\n[thin-host]")
    # Nautilus verify script is archived. Thin-host tests run in [tests].
    check_v2_optional()

    print("\n=== summary ===")
    if all_errs:
        print(f"NOT READY ({len(all_errs)} issue(s)):")
        for e in all_errs:
            print(f" - {e}")
        if args.mode == "live":
            print(
                "\nLIVE remains blocked until every issue is cleared AND you give "
                "explicit in-session approval to create data/LIVE_APPROVED."
            )
        return 1

    print("READY for requested mode gates.")
    if args.mode == "live":
        print(
            "All automated gates green. Still requires EXPLICIT user approval "
            "before creating data/LIVE_APPROVED and flipping LIVE flags."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
