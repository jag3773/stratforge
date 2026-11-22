#!/usr/bin/env python3
"""
Paper + LIVE sleeve status reporter.
Prefers node_health equity (current MTM); journal is fallback + trade history.
LIVE block is appended when LIVE_APPROVED or live journal/health exists.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(
    os.environ.get("PRO_TRADER_ROOT")
    or os.environ.get("NAUTILUS_ROOT")
    or Path(__file__).resolve().parents[1]
)
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from strategies.display_time import date_et, format_et  # noqa: E402

NO_DAY_MARK = "no day mark yet"


def display_ts(value) -> str:
    return format_et(value) or (str(value) if value else "")


JOURNAL = ROOT / "data" / "execution_log.jsonl"
NODE_HEALTH = ROOT / "data" / "node_health.json"
FEED_HEALTH = ROOT / "data" / "feed_health.json"
LIVE_JOURNAL = ROOT / "data" / "execution_log_live.jsonl"
LIVE_NODE_HEALTH = ROOT / "data" / "node_health_live.json"
LIVE_FEED_HEALTH = ROOT / "data" / "feed_health_live.json"
LIVE_APPROVED = ROOT / "data" / "LIVE_APPROVED"
LIVE_CFG = ROOT / "config" / "live_micro.json"
LIVE_FILL_ACTIONS = {"LIVE_BUY", "LIVE_CLOSE", "LIVE_CLOSE_PARTIAL"}


def load_journal():
    if not JOURNAL.exists():
        return []
    entries = []
    for line in JOURNAL.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def compute_status(entries):
    cash = None
    positions = {}
    last_equity = None
    all_trades = []
    opening = None
    deposits = []
    contributed = None
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    for e in entries:
        action = e.get("action")
        ts_str = e.get("ts")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else now
        except Exception:
            ts = now

        if action == "OPENING_BALANCE":
            opening = e
            cash = float(e.get("capital", 1000.0))
            contributed = cash
            last_equity = cash
            continue

        if action == "CASH_DEPOSIT":
            amt = float(e.get("amount") or e.get("capital") or 0.0)
            if cash is None:
                cash = 0.0
            if "cash" in e and e["cash"] is not None:
                cash = float(e["cash"])
            else:
                cash += amt
            if contributed is None:
                contributed = cash
            else:
                contributed += amt
            if e.get("equity") is not None:
                last_equity = e.get("equity")
            else:
                last_equity = (last_equity or cash)
            deposits.append({**e, "ts_dt": ts, "amount": amt})
            continue

        if action == "PAPER_BUY":
            sym = e["symbol"]
            qty = float(e["qty"])
            price = float(e["price"])
            fee = float(e.get("fee", 0))
            notional = float(e.get("notional", qty * price))
            if cash is None:
                cash = 1000.0
            if "cash" in e and e["cash"] is not None:
                cash = float(e["cash"])
            else:
                cash -= notional + fee
            positions[sym] = {"qty": qty, "entry_price": price}
            all_trades.append({**e, "ts_dt": ts})
            last_equity = e.get("equity", last_equity)

        elif action == "PAPER_CLOSE":
            sym = e["symbol"]
            qty = float(e["qty"])
            price = float(e["price"])
            fee = float(e.get("fee", 0))
            notional = float(e.get("notional", qty * price))
            if cash is not None:
                if "cash" in e and e["cash"] is not None:
                    cash = float(e["cash"])
                else:
                    cash += notional - fee
            positions.pop(sym, None)
            all_trades.append({**e, "ts_dt": ts})
            last_equity = e.get("equity", last_equity)

        elif action == "EQUITY_SNAPSHOT":
            last_equity = e.get("equity", last_equity)

    recent = [t for t in all_trades if t.get("ts_dt", now) >= cutoff]
    equity = last_equity if last_equity is not None else (cash if cash is not None else 1000.0)
    if contributed is None:
        contributed = float(opening.get("capital", 1000.0)) if opening else 1000.0
    return {
        "equity": round(float(equity), 2),
        "cash": round(float(cash), 2) if cash is not None else 1000.0,
        "positions": positions,
        "recent_trades": recent,
        "total_trades": len(all_trades),
        "opening": opening,
        "deposits": deposits,
        "contributed_capital": round(float(contributed), 2),
        "total_deposits": round(sum(float(d.get("amount") or 0) for d in deposits), 2),
    }


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def load_journal_path(path: Path):
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def _parse_ts(ts_str, default):
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else default
    except Exception:
        return default


def compute_live_status(entries, now=None):
    """Book + 24h fills from the LIVE journal. Node health overrides MTM.

    EXECUTOR_START.start_usd is config opening (stays $250 after venue adds).
    Contributed capital = opening + CASH_DEPOSIT − CASH_WITHDRAW.
    start_date is OPENING_BALANCE.start_date if present, else the ET calendar
    date of the first LIVE_BUY (not EXECUTOR_START — that predates fills).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    cash = None
    opening_usd = None
    contributed = None
    deposits = []
    positions = {}
    fills = []
    day_start_equity = None
    last_equity = None
    opening_start_date = None
    first_fill_ts = None
    for e in entries:
        action = e.get("action")
        ts = _parse_ts(e.get("ts"), now)
        if action == "EXECUTOR_START":
            su = float(e.get("start_usd") or 250.0)
            if opening_usd is None:
                opening_usd = su
                contributed = su
            if cash is None:
                cash = su
                last_equity = cash
            continue
        if action == "OPENING_BALANCE":
            su = float(e.get("capital") or opening_usd or 250.0)
            opening_usd = su
            contributed = su
            cash = su
            last_equity = cash
            if e.get("start_date"):
                opening_start_date = str(e["start_date"])
            elif not opening_start_date:
                opening_start_date = date_et(ts) or None
            continue
        if action == "CASH_DEPOSIT":
            amt = float(e.get("amount") or e.get("capital") or 0.0)
            if e.get("cash") is not None:
                cash = float(e["cash"])
            else:
                cash = float(cash or 0.0) + amt
            if e.get("start_usd") is not None:
                contributed = float(e["start_usd"])
            else:
                contributed = float(contributed if contributed is not None else (opening_usd or 0.0)) + amt
            if e.get("equity") is not None:
                last_equity = float(e["equity"])
            deposits.append({**e, "ts_dt": ts, "amount": amt})
            continue
        if action == "CASH_WITHDRAW":
            amt = float(e.get("amount") or e.get("capital") or 0.0)
            if e.get("cash") is not None:
                cash = float(e["cash"])
            else:
                cash = max(0.0, float(cash or 0.0) - amt)
            if e.get("start_usd") is not None:
                contributed = float(e["start_usd"])
            else:
                contributed = max(
                    0.0,
                    float(contributed if contributed is not None else (opening_usd or 0.0)) - amt,
                )
            if e.get("equity") is not None:
                last_equity = float(e["equity"])
            continue
        if action == "BOOK_SYNC":
            if e.get("cash") is not None:
                cash = float(e["cash"])
            continue
        if action == "RECONCILE" and e.get("ok"):
            if e.get("local_cash") is not None:
                cash = float(e["local_cash"])
            if e.get("exchange_cash") is not None and cash is None:
                cash = float(e["exchange_cash"])
            continue
        if action == "DAY_OPEN":
            if e.get("equity") is not None:
                day_start_equity = float(e["equity"])
            elif e.get("day_start_equity") is not None:
                day_start_equity = float(e["day_start_equity"])
            continue
        if action in LIVE_FILL_ACTIONS:
            sym = e.get("symbol")
            qty = float(e.get("qty") or 0.0)
            price = float(e.get("price") or 0.0)
            if e.get("cash") is not None:
                cash = float(e["cash"])
            if e.get("equity") is not None:
                last_equity = float(e["equity"])
            fills.append({**e, "ts_dt": ts})
            if action == "LIVE_BUY" and first_fill_ts is None:
                first_fill_ts = ts
            if action == "LIVE_BUY" and sym:
                positions[sym] = {"qty": qty, "entry_price": price}
            elif action == "LIVE_CLOSE" and sym:
                positions.pop(sym, None)
            elif action == "LIVE_CLOSE_PARTIAL" and sym and sym in positions:
                remain = e.get("remaining_qty")
                if remain is not None:
                    positions[sym]["qty"] = float(remain)
                else:
                    positions[sym]["qty"] = max(0.0, positions[sym]["qty"] - qty)
                    if positions[sym]["qty"] <= 1e-12:
                        positions.pop(sym, None)
    recent = [t for t in fills if t.get("ts_dt", now) >= cutoff]
    if opening_usd is None:
        opening_usd = 250.0
    if contributed is None:
        contributed = opening_usd
    if cash is None:
        cash = opening_usd
    if last_equity is None:
        last_equity = cash
    if opening_start_date:
        start_date = opening_start_date
    elif first_fill_ts is not None:
        start_date = date_et(first_fill_ts) or "n/a"
    else:
        start_date = "n/a"
    return {
        "start_usd": float(opening_usd),
        "start_date": start_date,
        "contributed_capital": float(contributed),
        "total_deposits": round(sum(float(d.get("amount") or 0) for d in deposits), 2),
        "deposits": deposits,
        "cash": float(cash),
        "equity": float(last_equity),
        "positions": positions,
        "recent_fills": recent,
        "total_fills": len(fills),
        "day_start_equity": day_start_equity,
    }


def live_halt_usd(day_start_equity: float | None = None) -> float:
    from risk_limits import limits_from_config, effective_daily_loss_halt

    cfg = load_json(LIVE_CFG) or {}
    lim = limits_from_config(cfg)
    halt = effective_daily_loss_halt(lim, day_start_equity)
    return float(halt) if halt is not None else 0.0


def format_live_sleeve(now=None) -> str | None:
    """Telegram-plain LIVE block, or None if sleeve is not in play."""
    approved = LIVE_APPROVED.is_file()
    entries = load_journal_path(LIVE_JOURNAL)
    nh = load_json(LIVE_NODE_HEALTH)
    fh = load_json(LIVE_FEED_HEALTH)
    if not approved and not entries and not nh:
        return None
    book = compute_live_status(entries, now=now)
    if nh and nh.get("equity") is not None:
        portfolio = float(nh["equity"])
        cash = float(nh.get("cash", book["cash"]))
        pos_src = nh.get("positions") if isinstance(nh.get("positions"), list) else None
    else:
        portfolio = book["equity"]
        cash = book["cash"]
        pos_src = None
    opening = float(book["start_usd"])
    contributed = float(book.get("contributed_capital") or opening)
    deposits_total = float(book.get("total_deposits") or 0.0)
    pnl = portfolio - contributed
    pnl_pct = (pnl / contributed * 100.0) if contributed else 0.0
    halt = live_halt_usd(book["day_start_equity"])
    day_start = book["day_start_equity"]
    if day_start is not None:
        daily_loss = max(0.0, day_start - portfolio)
        daily_line = f"Daily MTM loss: ${daily_loss:.2f} / halt ${halt:.2f}"
    else:
        daily_line = f"Daily MTM loss: n/a ({NO_DAY_MARK}) / halt ${halt:.2f}"
    strategy = (nh or {}).get("strategy") or "trend_rotation_25_max8_rotate"
    lines = [
        "=== LIVE micro sleeve ===",
        f"Strategy: {strategy} (LIVE)",
        f"Start capital: ${opening:.2f} on {book.get('start_date') or 'n/a'}",
    ]
    if deposits_total:
        lines.append(
            f"Deposits: ${deposits_total:.2f}  → contributed ${contributed:.2f}"
        )
    lines.extend(
        [
            f"Portfolio value: ${portfolio:.2f}  ({pnl:+.2f} / {pnl_pct:+.2f}% vs contributed)",
            f"Cash: ${cash:.2f}",
            daily_line,
        ]
    )
    if nh:
        lines.append(
            f"Node: status={nh.get('status')} bars={nh.get('bars_seen')} "
            f"n={nh.get('n_positions')} ts={display_ts(nh.get('ts'))}"
        )
    else:
        lines.append("Node: unavailable (journal fallback)")
    if fh:
        lines.append(
            f"Feed: cycle={fh.get('cycle')} emitted={fh.get('bars_emitted')} "
            f"ok={fh.get('fetch_ok')} fail={fh.get('fetch_fail')} ts={display_ts(fh.get('ts'))}"
        )
    lines.append("Open Positions:")
    if isinstance(pos_src, list):
        if pos_src:
            for sym in pos_src:
                lines.append(f"  {sym}")
        else:
            lines.append("  (none)")
    elif book["positions"]:
        for sym, p in book["positions"].items():
            lines.append(f"  {sym}: {p['qty']:.6g} @ ${p.get('entry_price', 0):.6g}")
    else:
        lines.append("  (none)")
    lines.append(f"Trades in last 24h: {len(book['recent_fills'])}")
    for t in book["recent_fills"][-10:]:
        act = t.get("action", "")
        sym = t.get("symbol", "")
        price = t.get("price")
        pnl_t = t.get("pnl")
        reason = t.get("reason", "")
        if "BUY" in act:
            lines.append(f"  BUY {sym} @ ${price} ({reason})")
        elif "CLOSE" in act:
            extra = f" PnL=${pnl_t}" if pnl_t is not None else ""
            lines.append(f"  CLOSE {sym} @ ${price}{extra} ({reason})")
    if not approved:
        lines.append("Note: LIVE_APPROVED missing — leftover files only")
    return "\n".join(lines)


def main():
    entries = load_journal()
    status = compute_status(entries)
    opening = status.get("opening") or {}
    nh = load_json(NODE_HEALTH)
    fh = load_json(FEED_HEALTH)

    # Single portfolio value: live node MTM when available, else journal last stamp
    if nh and nh.get("equity") is not None:
        portfolio = float(nh["equity"])
        cash = float(nh.get("cash", status["cash"]))
        equity_src = "node"
    else:
        portfolio = float(status["equity"])
        cash = float(status["cash"])
        equity_src = "journal"

    strategy = (
        (nh or {}).get("strategy")
        or "trend_rotation_25_max8_rotate"
    )
    start_cap = float(opening.get("capital", 1000))
    start_date = opening.get("start_date", "n/a")
    contributed = float(status.get("contributed_capital") or start_cap)
    deposits_total = float(status.get("total_deposits") or 0.0)
    pnl = portfolio - contributed
    pnl_pct = (pnl / contributed * 100.0) if contributed else 0.0

    print("=== Paper Trading Status ===")
    print(f"Strategy: {strategy} (paper)")
    print(f"Start capital: ${start_cap:.2f} on {start_date}")
    if deposits_total:
        print(f"Deposits: ${deposits_total:.2f}  → contributed ${contributed:.2f}")
    print(f"Portfolio value: ${portfolio:.2f}  ({pnl:+.2f} / {pnl_pct:+.2f}% vs contributed)")
    print(f"Cash: ${cash:.2f}")

    if nh:
        print(
            f"Node: bars={nh.get('bars_seen')} trades={nh.get('trades')} "
            f"last={nh.get('last_bar_symbol')} @ {display_ts(nh.get('ts'))}"
        )
    elif equity_src == "journal":
        print("Node: unavailable (showing journal fallback)")

    if fh:
        print(
            f"Feed: cycle={fh.get('cycle')} emitted={fh.get('bars_emitted')} "
            f"ok={fh.get('fetch_ok')} fail={fh.get('fetch_fail')} ts={display_ts(fh.get('ts'))}"
        )

    print("\nOpen Positions:")
    pos_list = (nh or {}).get("positions")
    if isinstance(pos_list, list) and pos_list and not status["positions"]:
        for sym in pos_list:
            print(f"  {sym}")
    elif status["positions"]:
        for sym, p in status["positions"].items():
            print(f"  {sym}: {p['qty']:.6g} @ ${p.get('entry_price', 0):.6g}")
    else:
        print("  (none)")

    print(f"\nTrades in last 24h: {len(status['recent_trades'])}")
    for t in status["recent_trades"][-10:]:
        act = t.get("action", "")
        sym = t.get("symbol", "")
        price = t.get("price")
        pnl_t = t.get("pnl")
        reason = t.get("reason", "")
        if "BUY" in act:
            print(f"  BUY {sym} @ ${price} ({reason})")
        elif "CLOSE" in act:
            print(f"  CLOSE {sym} @ ${price} PnL=${pnl_t} ({reason})")

    print(f"\nTotal lifetime trades: {status['total_trades']}")
    print("=====================================")
    live_block = format_live_sleeve()
    if live_block:
        print()
        print(live_block)


if __name__ == "__main__":
    main()
