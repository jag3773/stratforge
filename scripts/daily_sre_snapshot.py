#!/usr/bin/env python3
"""Read-only daily SRE snapshot. No mutations. No secrets."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("NAUTILUS_ROOT", Path(__file__).resolve().parents[1]))
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))
import stratforge  # noqa: E402
from strategies.display_time import format_et, next_4h_close, next_4h_close_et  # noqa: E402
JOBS_JSON = Path(
    os.environ.get(
        "HERMES_CRON_JOBS",
        str(Path.home() / ".hermes/profiles/trader/cron/jobs.json"),
    )
)
WATCH_JOBS = {
    "78d3b112f9f8",  # trade alerts
    "ef4763615c58",  # ops watchdog
    "ec796b694ddc",  # feed health
    "a0268ddb3699",  # journal backup
    "5c85c8ae2161",  # morning status
    "06797bd8c192",  # parity (paused)
    "6d27b6d39365",  # daily SRE agent
    "7e4d7cc97335",  # daily SRE deliver
}
UNIT_PROPS = (
    "Id,ActiveState,SubState,NRestarts,MainPID,MemoryCurrent,MemoryMax,"
    "FragmentPath,Result,ExecMainStatus,ExecMainCode"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(iso: str | None) -> str:
    if not iso:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        sec = (_now() - dt).total_seconds()
        if sec < 120:
            return f"{sec:.0f}s"
        if sec < 7200:
            return f"{sec/60:.0f}m"
        return f"{sec/3600:.1f}h"
    except Exception:
        return "unparseable"


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        return r.returncode, out.strip()
    except Exception as e:
        return 99, str(e)


def _load(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        return {"_error": str(e)}


def _section(title: str) -> None:
    print(f"\n## {title}")


def systemd_units() -> None:
    _section("systemd")
    for unit in ("stratforge-paper.service", "stratforge-live.service"):
        code, out = _run(["systemctl", "--user", "show", unit, "-p", UNIT_PROPS])
        print(f"### {unit} (show_exit={code})")
        print(out or "(no output)")


def processes() -> None:
    _section("live_node processes")
    code, out = _run(["ps", "-eo", "pid,etime,pcpu,pmem,wchan:24,nlwp,stat,cmd"])
    lines = [ln for ln in (out or "").splitlines() if "live_node.py" in ln and "pgrep" not in ln]
    if not lines:
        print("(none)")
        return
    print("PID ELAPSED %CPU %MEM WCHAN NLWP STAT CMD")
    for ln in lines:
        print(ln)


def gates() -> None:
    _section("gates")
    data = ROOT / "data"
    print(f"LIVE_APPROVED={'yes' if (data / 'LIVE_APPROVED').is_file() else 'no'}")
    halts = []
    for name in (
        "TRADING_HALTED",
        "TRADING_HALTED_paper",
        "TRADING_HALTED_live",
    ):
        if (data / name).is_file():
            halts.append(name)
    print("halt_files=" + (",".join(halts) if halts else "(none)"))


def health_file(rel: str) -> None:
    p = ROOT / "data" / rel
    d = _load(p)
    if d is None:
        print(f"{rel}: MISSING")
        return
    if isinstance(d, dict) and d.get("_error"):
        print(f"{rel}: unreadable {d['_error']}")
        return
    keys = (
        "ts",
        "started_at",
        "mode",
        "status",
        "strategy",
        "cycle",
        "bars_seen",
        "bars_emitted",
        "fetch_ok",
        "fetch_fail",
        "cash",
        "equity",
        "n_positions",
        "positions",
        "trades",
    )
    slim = {k: d.get(k) for k in keys if k in d}
    slim["age"] = _age(d.get("ts"))
    print(f"{rel}: {json.dumps(slim, default=str)}")


def health() -> None:
    _section("health")
    for rel in (
        "feed_health.json",
        "node_health.json",
        "feed_health_live.json",
        "node_health_live.json",
    ):
        health_file(rel)


def journal_tail(rel: str, n: int = 15) -> None:
    p = ROOT / "data" / rel
    if not p.is_file():
        print(f"{rel}: MISSING")
        return
    lines = [ln.strip() for ln in p.read_text().splitlines() if ln.strip()]
    print(f"{rel}: bytes={p.stat().st_size} lines={len(lines)}")
    for ln in lines[-n:]:
        try:
            e = json.loads(ln)
        except Exception:
            print("  (bad json line)")
            continue
        print(
            f"  {e.get('ts', '')} {e.get('action', '?')} {e.get('symbol') or ''} {_extra(e)}".rstrip()
        )


def _extra(e: dict) -> str:
    parts = []
    for k in ("ok", "side", "qty", "cash", "equity", "error"):
        if e.get(k) is not None:
            parts.append(f"{k}={e.get(k)}")
    reason = e.get("reason") or e.get("reason_signal")
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts)


def journals() -> None:
    _section("journals (last 15)")
    journal_tail("execution_log_live.jsonl")
    journal_tail("execution_log.jsonl", n=8)


def next_4h() -> None:
    _section("clock")
    now = _now()
    nxt = next_4h_close(now)
    print(f"now={format_et(now)}")
    print(f"next_4h_close={next_4h_close_et(now)}")
    print(f"minutes_to_close={(nxt - now).total_seconds() / 60:.1f}")
    code, out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"])
    print(out)
    code, out = _run(["loginctl", "show-user", os.environ.get("USER", "operator"), "-p", "Linger"])
    print(out)


def host() -> None:
    _section("host")
    code, out = _run(["df", "-h", "/"])
    print(out)
    code, out = _run(["free", "-h"])
    print(out)
    code, out = _run(["uptime"])
    print(out)
    backups = ROOT / "data" / "journal_backups"
    if backups.is_dir():
        lives = sorted(backups.glob("execution_log_live.jsonl.*"))
        papers = sorted(backups.glob("execution_log.jsonl.*"))
        def _newest(files):
            if not files:
                return "none"
            f = files[-1]
            age = _now().timestamp() - f.stat().st_mtime
            return f"{f.name} age_h={age/3600:.1f}"
        print(f"journal_backup_live={_newest(lives)}")
        print(f"journal_backup_paper={_newest(papers)}")
    else:
        print("journal_backups: MISSING dir")


def scripts() -> None:
    _section("watchdog_scripts")
    py = ROOT / ".venv" / "bin" / "python"
    exe = str(py) if py.is_file() else "python3"
    for rel in ("scripts/ops_watchdog.py",):
        code, out = _run([exe, str(ROOT / rel)], timeout=30)
        print(f"{rel}: exit={code}")
        if out:
            print(out[:800])


def crons() -> None:
    _section("hermes_crons")
    data = _load(JOBS_JSON)
    if not data or not isinstance(data, dict):
        print(f"jobs.json unreadable path={JOBS_JSON}")
        return
    jobs = data.get("jobs") or []
    by_id = {j.get("id"): j for j in jobs if isinstance(j, dict)}
    for jid in sorted(WATCH_JOBS):
        j = by_id.get(jid)
        if not j:
            print(f"{jid}: NOT IN jobs.json")
            continue
        print(
            f"{jid} name={j.get('name')} enabled={j.get('enabled')} "
            f"state={j.get('state')} last_status={j.get('last_status')} "
            f"last_run={j.get('last_run_at')} last_error={j.get('last_error')} "
            f"deliver_err={j.get('last_delivery_error')}"
        )


def cursor() -> None:
    _section("trade_alert_cursor")
    d = _load(ROOT / "data" / "trade_alert_cursor.json")
    print(json.dumps(d, indent=2) if d else "MISSING")
    for rel in (
        "execution_log.jsonl",
        "execution_log_live.jsonl",
    ):
        p = ROOT / "data" / rel
        if p.is_file() and isinstance(d, dict):
            key = str(p)
            state = d.get(key)
            off = state.get("offset") if isinstance(state, dict) else None
            sz = p.stat().st_size
            print(f"{rel}: cursor={off} size={sz} delta={(sz - off) if isinstance(off, int) else 'n/a'}")


def live_unit_cmd() -> None:
    _section("live_unit_exec")
    # ExecStart only — do not dump Environment= (may contain .env.live).
    code, out = _run(
        [
            "systemctl",
            "--user",
            "show",
            "stratforge-live.service",
            "-p",
            "FragmentPath",
            "-p",
            "ExecStart",
        ]
    )
    print(out)


def main() -> int:
    print(f"SRE_SNAPSHOT now={format_et(_now())} root={ROOT}")
    systemd_units()
    processes()
    gates()
    health()
    journals()
    next_4h()
    host()
    scripts()
    crons()
    cursor()
    live_unit_cmd()
    print("\n## end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
