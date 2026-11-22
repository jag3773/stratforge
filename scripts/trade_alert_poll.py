#!/usr/bin/env python3
"""
Trade alert poller for Hermes no_agent cron.

Reads paper/live execution journals; emits Telegram-friendly text only
when new fill lines appear after the saved byte cursor.

Silent contract (Hermes no_agent):
  - empty stdout => no delivery (no spam)
  - non-empty stdout => delivered verbatim
  - non-zero exit => error alert from scheduler

First run / missing cursor: bootstrap to EOF without replaying history.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("NAUTILUS_ROOT", Path(__file__).resolve().parents[1]))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.display_time import format_et  # noqa: E402

CURSOR_PATH = Path(
    os.environ.get(
        "TRADE_ALERT_CURSOR",
        str(ROOT / "data" / "trade_alert_cursor.json"),
    )
)

JOURNAL_PATHS = [
    ROOT / "data" / "execution_log.jsonl",
    ROOT / "data" / "execution_log_live.jsonl",
    ROOT / "data" / "execution_log_donch.jsonl",
]

# Fills + live-critical order lifecycle. FEE_ESTIMATED is booked next to
# LIVE_BUY/CLOSE and is noisy — do not page it.
ALERT_ACTIONS = {
    "PAPER_BUY",
    "PAPER_CLOSE",
    "SHADOW_BUY",
    "SHADOW_CLOSE",
    "SHADOW_CLOSE_PARTIAL",
    "LIVE_BUY",
    "LIVE_CLOSE",
    "LIVE_CLOSE_PARTIAL",
    "ORDER_SUBMIT",
    "ORDER_EMPTY_FILL",
    "ORDER_INCOMPLETE_FILL",
    "RISK_DENY",
    "HALT_ENGAGED",
    "DAILY_LOSS_HALT",
    "ORDER_ERROR",
    "RECONCILE_MISMATCH",
    "CASH_OVERDRAFT",
    "LIVE_ORDER_FAILED",
    "EXEC_UNAVAILABLE",
    "BUY_RESIZE_INSUFFICIENT_FUND",
}


def _load_cursor() -> dict:
    if not CURSOR_PATH.exists():
        return {}
    try:
        data = json.loads(CURSOR_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cursor(cur: dict) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CURSOR_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n")
    tmp.replace(CURSOR_PATH)


def prune_cursor(cur: dict, journal_paths: list[Path] | None = None) -> dict:
    """Drop offsets for journals we no longer poll (retired shadow, etc.)."""
    keep = {str(p) for p in (journal_paths if journal_paths is not None else JOURNAL_PATHS)}
    return {k: v for k, v in cur.items() if k in keep}


def _fmt_num(x, digits: int = 6) -> str:
    try:
        v = float(x)
    except Exception:
        return str(x)
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    if abs(v) >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:.{digits}g}"


def _side_label(action: str) -> str:
    if action.endswith("_BUY"):
        return "BUY"
    if action.endswith("_CLOSE"):
        return "SELL/CLOSE"
    return action


def _mode_label(entry: dict, action: str) -> str:
    mode = (entry.get("mode") or "").upper()
    if mode in {"PAPER", "SHADOW", "LIVE"}:
        return mode
    if action.startswith("PAPER_"):
        return "PAPER"
    if action.startswith("SHADOW_"):
        return "SHADOW"
    if action.startswith("LIVE_"):
        return "LIVE"
    return mode or "UNKNOWN"


_EVENT_ACTIONS = {
    "ORDER_SUBMIT",
    "ORDER_EMPTY_FILL",
    "ORDER_INCOMPLETE_FILL",
    "RISK_DENY",
    "HALT_ENGAGED",
    "DAILY_LOSS_HALT",
    "ORDER_ERROR",
    "RECONCILE_MISMATCH",
    "CASH_OVERDRAFT",
    "LIVE_ORDER_FAILED",
    "EXEC_UNAVAILABLE",
    "BUY_RESIZE_INSUFFICIENT_FUND",
}


def format_alert(entry: dict) -> str:
    action = str(entry.get("action", ""))
    mode = _mode_label(entry, action)
    side = _side_label(action)
    sym = entry.get("symbol", "")
    price = entry.get("price")
    qty = entry.get("qty")
    notional = entry.get("notional")
    fee = entry.get("fee")
    reason = entry.get("reason") or entry.get("signal") or entry.get("reason_signal") or ""
    cash = entry.get("cash")
    equity = entry.get("equity")
    pnl = entry.get("pnl")
    ts = entry.get("ts", "")
    error = entry.get("error")
    reasons = entry.get("reasons")
    coid = entry.get("client_order_id")

    if action in _EVENT_ACTIONS:
        head = f"**{mode} {action}**"
        if sym:
            head = f"{head} {sym}"
        lines = [head]
        if action == "ORDER_SUBMIT":
            sub_side = entry.get("side") or ""
            bits = [x for x in (sub_side, ) if x]
            if qty is not None and price is not None:
                bits.append(f"qty {_fmt_num(qty)} @ ${_fmt_num(price)}")
            elif qty is not None:
                bits.append(f"qty {_fmt_num(qty)}")
            if bits:
                lines.append(" ".join(bits))
        elif qty is not None and price is not None:
            lines.append(f"qty {_fmt_num(qty)} @ ${_fmt_num(price)}")
        if reasons:
            if isinstance(reasons, (list, tuple)):
                lines.append("reasons: " + "; ".join(str(r) for r in reasons))
            else:
                lines.append(f"reasons: {reasons}")
        if error:
            lines.append(f"error: {error}")
        if reason:
            lines.append(f"reason: {reason}")
        if entry.get("daily_loss_usd") is not None:
            lines.append(f"daily MTM ${_fmt_num(entry.get('daily_loss_usd'), 2)}")
        if coid:
            lines.append(f"coid {coid}")
        if ts:
            shown = format_et(ts) or str(ts)
            lines.append(f"ts {shown}")
        return "\n".join(lines)

    lines = [
        f"**{mode} {side}** {sym or '?'}",
        f"qty {_fmt_num(qty)} @ ${_fmt_num(price)}",
    ]
    if notional is not None:
        lines.append(f"notional ${_fmt_num(notional, 2)}  fee ${_fmt_num(fee or 0, 4)}")
    if pnl is not None and "CLOSE" in action:
        try:
            p = float(pnl)
            sign = "+" if p >= 0 else ""
            lines.append(f"pnl {sign}${_fmt_num(p, 4)}")
        except Exception:
            lines.append(f"pnl {pnl}")
    if reason:
        lines.append(f"reason: {reason}")
    book = []
    if cash is not None:
        book.append(f"cash ${_fmt_num(cash, 2)}")
    if equity is not None:
        book.append(f"equity ${_fmt_num(equity, 2)}")
    if book:
        lines.append(" · ".join(book))
    if ts:
        shown = format_et(ts) or str(ts)
        lines.append(f"ts {shown}")
    return "\n".join(lines)


def _read_new_lines(path: Path, start_offset: int) -> tuple[list[str], int]:
    """Return (new_lines, new_offset).

    Truncation/rewrite (offset > size): reseat at EOF with *no* replay so
    journal edits don't spam historical fills. Pure append is the normal path.
    """
    size = path.stat().st_size
    if start_offset > size:
        # truncated or replaced — bootstrap to EOF, silent
        return [], size
    if start_offset == size:
        return [], size
    lines: list[str] = []
    with path.open("rb") as f:
        f.seek(start_offset)
        data = f.read()
        new_offset = f.tell()
    # Only advance past complete lines; leave partial trailing line for next poll
    if data and not data.endswith(b"\n"):
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            return [], start_offset
        complete = data[: last_nl + 1]
        new_offset = start_offset + len(complete)
        data = complete
    text = data.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)
    return lines, new_offset


def poll_one(path: Path, cur: dict, *, force_bootstrap: bool) -> list[str]:
    key = str(path)
    if not path.exists():
        cur.pop(key, None)
        return []

    size = path.stat().st_size
    state = cur.get(key) or {}
    offset = int(state.get("offset", -1))

    # First seen or forced bootstrap: seed at EOF, no historical spam
    if force_bootstrap or offset < 0:
        cur[key] = {"offset": size}
        return []

    raw_lines, new_offset = _read_new_lines(path, offset)
    alerts: list[str] = []
    for line in raw_lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        action = str(entry.get("action", ""))
        if action not in ALERT_ACTIONS:
            continue
        text = format_alert(entry)
        if "donch" in path.name:
            text = text.replace("**PAPER ", "**DONCH PAPER ", 1)
        alerts.append(text)

    cur[key] = {"offset": new_offset}
    return alerts


def main() -> int:
    # Optional force-bootstrap: TRADE_ALERT_BOOTSTRAP=1
    force_boot = os.environ.get("TRADE_ALERT_BOOTSTRAP", "0") == "1"
    cur = {} if force_boot else prune_cursor(_load_cursor())

    all_alerts: list[str] = []
    for path in JOURNAL_PATHS:
        try:
            all_alerts.extend(poll_one(path, cur, force_bootstrap=force_boot))
        except Exception as exc:
            # Hard failure on a journal should surface (non-zero + message)
            print(f"TRADE_ALERT ERROR reading {path}: {exc}", file=sys.stderr)
            print(f"TRADE_ALERT ERROR reading {path}: {exc}")
            return 1

    try:
        _save_cursor(cur)
    except Exception as exc:
        print(f"TRADE_ALERT ERROR saving cursor: {exc}", file=sys.stderr)
        print(f"TRADE_ALERT ERROR saving cursor: {exc}")
        return 1

    if not all_alerts:
        # Silent success — Hermes no_agent delivers nothing
        return 0

    # One message body; separate fills with a blank line
    print("\n\n".join(all_alerts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
