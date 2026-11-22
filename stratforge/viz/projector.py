"""Fold journals, node_health, and committed rollups into viz snapshots.

Read-only. Does not import live_node, does not call venues, does not invent fees.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stratforge.state import state_root

_ROOT = state_root()

from strategies.config_load import DNA_KEYS, load_config
from strategies.display_time import format_et
from scripts.paper_status import compute_live_status, compute_status

DNA_PATH = "config/trend_rotation_25_max8_rotate.json"
PAPER_CFG = "config/live_paper.json"
LIVE_CFG = "config/live_micro.json"

KNOB_KEYS = ("strategy_id",) + DNA_KEYS

RUN_ROLES = {
    "monthly_history_max8_rotate": "PRODUCTION",
    "monthly_history_max8_rotate_clear19": "RESEARCH",
    "monthly_history": "RESEARCH",
    "donch_d20_10": "RESEARCH",
    "monthly_history_max4": "REJECTED",
    "defensive": "REJECTED",
    "legacy_levered_margin": "INVALID",
    "recent_windows": "RESEARCH",
}

PAPER_FILLS = {"PAPER_BUY", "PAPER_CLOSE", "PAPER_CLOSE_PARTIAL"}
LIVE_FILLS = {"LIVE_BUY", "LIVE_CLOSE", "LIVE_CLOSE_PARTIAL"}
FILL_FIELDS = ("action", "symbol", "qty", "price", "fee", "cost", "notional", "equity", "cash", "reason")


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _load_journal(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _knobs(cfg: dict) -> dict[str, Any]:
    return {k: cfg[k] for k in KNOB_KEYS if k in cfg}


def _clock_et(value: Any) -> str:
    """Eastern clock, or empty. Prices must not become 1970-era timestamps."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and abs(float(value)) < 1_000_000_000:
        return ""
    return format_et(value) or ""


def _sleeve_knobs(root: Path, rel: str, fallback: dict[str, Any]) -> dict[str, Any]:
    path = root / rel
    if not path.is_file():
        return dict(fallback)
    loaded = _knobs(load_config(path, root=root))
    return loaded or dict(fallback)


def _knob_diff(sleeve: dict[str, Any], dna: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in KNOB_KEYS:
        if k not in sleeve and k not in dna:
            continue
        if sleeve.get(k) != dna.get(k):
            out[k] = {"sleeve": sleeve.get(k), "dna": dna.get(k)}
    return out


def _halt_files(data: Path, mode: str) -> list[str]:
    names = ["TRADING_HALTED", f"TRADING_HALTED_{mode}"]
    return [n for n in names if (data / n).is_file()]


def _compact_fill(entry: dict) -> dict[str, Any]:
    row: dict[str, Any] = {}
    ts = entry.get("ts")
    if ts:
        row["ts"] = ts
        et = format_et(ts)
        if et:
            row["ts_et"] = et
    for k in FILL_FIELDS:
        if k not in entry:
            continue
        v = entry[k]
        if v is None:
            continue
        row[k] = v
    return row


def _equity_points(entries: list[dict]) -> list[dict[str, Any]]:
    pts: list[dict[str, Any]] = []
    last = None
    for e in entries:
        eq = e.get("equity")
        ts = e.get("ts")
        if eq is None or not ts:
            continue
        val = round(float(eq), 2)
        # Chart time is unix seconds; keep last mark per second so the series is unique.
        sec = str(ts)[:19]
        if last == (sec, val):
            continue
        last = (sec, val)
        pt = {"t": ts, "equity": val}
        et = format_et(ts)
        if et:
            pt["t_et"] = et
        if pts and str(pts[-1]["t"])[:19] == sec:
            pts[-1] = pt
            continue
        pts.append(pt)
    if len(pts) > 2000:
        step = len(pts) // 2000 + 1
        pts = pts[::step] + ([pts[-1]] if pts[-1] is not pts[::step][-1] else [])
    return pts


def _append_live_mark(
    pts: list[dict[str, Any]], health: dict | None, equity: float
) -> list[dict[str, Any]]:
    """End the chart on node_health so the line matches the equity card."""
    if not health or health.get("ts") is None:
        return pts
    ts = health["ts"]
    val = round(float(equity), 2)
    pt: dict[str, Any] = {"t": ts, "equity": val}
    et = format_et(ts)
    if et:
        pt["t_et"] = et
    sec = str(ts)[:19]
    if pts:
        last = pts[-1]
        last_sec = str(last["t"])[:19]
        if last_sec == sec:
            pts[-1] = pt
            return pts
        if abs(float(last["equity"]) - val) < 0.01:
            return pts
        if last_sec > sec:
            return pts
    return pts + [pt]


def _positions(journal_pos: dict, health: dict | None) -> list[dict[str, Any]]:
    names: list[str] = []
    if health and isinstance(health.get("positions"), list):
        names = [str(s) for s in health["positions"]]
    for s in journal_pos:
        if s not in names:
            names.append(s)
    rows = []
    for s in names:
        rec = journal_pos.get(s) or {}
        row: dict[str, Any] = {"symbol": s}
        if rec.get("qty") is not None:
            row["qty"] = rec["qty"]
        if rec.get("entry_price") is not None:
            row["entry_price"] = rec["entry_price"]
        rows.append(row)
    return rows


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def _book(
    *,
    book_id: str,
    mode: str,
    entries: list[dict],
    health: dict | None,
    data_dir: Path,
    config_path: str,
    knobs: dict[str, Any],
    dna_knobs: dict[str, Any] | None = None,
    live: bool,
) -> dict[str, Any] | None:
    if not entries and not health:
        return None
    book = compute_live_status(entries) if live else compute_status(entries)
    if health and health.get("equity") is not None:
        equity = float(health["equity"])
        source = "node_health"
        cash = float(health["cash"]) if health.get("cash") is not None else float(book.get("cash") or 0)
    else:
        equity = float(book.get("equity") or 0)
        source = "journal"
        cash = float(book.get("cash") or 0)
    contributed = float(book.get("contributed_capital") or 0)
    as_of = (health or {}).get("ts") or (entries[-1].get("ts") if entries else None)
    halt = _halt_files(data_dir, book_id if book_id != "live" else "live")
    journal_pos = book.get("positions") or {}
    fills_src = book.get("recent_fills") if live else book.get("recent_trades")
    fill_actions = LIVE_FILLS if live else PAPER_FILLS
    all_fills = [_compact_fill(e) for e in entries if e.get("action") in fill_actions]
    now = {
        "id": book_id,
        "mode": mode,
        "strategy_id": knobs.get("strategy_id") or (health or {}).get("strategy"),
        "config_path": config_path,
        "knobs": knobs,
        "as_of_utc": as_of,
        "as_of_et": format_et(as_of) if as_of else "",
        "equity": round(equity, 2),
        "equity_source": source,
        "cash": round(cash, 2),
        "contributed": round(contributed, 2),
        "pnl": round(equity - contributed, 2),
        "pnl_pct": round((equity - contributed) / contributed * 100.0, 2) if contributed else 0.0,
        "positions": _positions(journal_pos, health),
        "n_positions": len(_positions(journal_pos, health)),
        "halted": bool(halt),
        "halt_files": halt,
        "last_bar_close": (health or {}).get("last_bar_close"),
        "last_bar_close_et": _clock_et((health or {}).get("last_bar_close")),
        "max_concurrent": knobs.get("max_concurrent"),
        "dna_path": DNA_PATH,
        "knob_diff": _knob_diff(knobs, dna_knobs or {}),
    }
    return {
        "now": now,
        "equity": {"points": _append_live_mark(_equity_points(entries), health, equity)},
        "fills": {"fills": all_fills[-100:]},
        "recent_24h": len(fills_src or []),
    }


def _summarize_rollup(rows: list[dict], *, run_id: str, knobs: dict[str, Any], config_path: str) -> dict[str, Any]:
    rets = [float(r["return_pct"]) for r in rows if r.get("return_pct") is not None]
    beats = sum(1 for r in rows if r.get("beats_btc") is True)
    open_max = max((int(r.get("open_n") or r.get("open_max") or 0) for r in rows), default=0)
    points = []
    for r in rows:
        if r.get("start") is None or r.get("return_pct") is None:
            continue
        pt = {
            "start": r["start"],
            "return_pct": r["return_pct"],
            "btc_return_pct": r.get("btc_return_pct", r.get("btc_pct")),
            "beats_btc": bool(r.get("beats_btc")),
        }
        points.append(pt)
    return {
        "id": run_id,
        "role": RUN_ROLES.get(run_id, "RESEARCH"),
        "strategy_id": knobs.get("strategy_id"),
        "config_path": config_path,
        "knobs": knobs,
        "n": len(rets),
        "avg_return_pct": round(statistics.fmean(rets), 4) if rets else None,
        "med_return_pct": round(statistics.median(rets), 4) if rets else None,
        "min_return_pct": min(rets) if rets else None,
        "max_return_pct": max(rets) if rets else None,
        "beats_btc": beats,
        "open_max_obs": open_max,
        "points": points,
    }


def project(*, root: Path, out: Path) -> Path:
    root = root.resolve()
    out = out.resolve()
    data = root / "data"
    dna_rel = DNA_PATH
    dna = load_config(root / dna_rel, root=root) if (root / dna_rel).is_file() else {}
    knobs = _knobs(dna)

    books_meta: list[dict[str, Any]] = []

    paper = _book(
        book_id="paper",
        mode="PAPER",
        entries=_load_journal(data / "execution_log.jsonl"),
        health=_load_json(data / "node_health.json"),
        data_dir=data,
        config_path=PAPER_CFG,
        knobs=_sleeve_knobs(root, PAPER_CFG, knobs),
        dna_knobs=knobs,
        live=False,
    )
    if paper:
        dest = out / "books" / "paper"
        _dump(dest / "now.json", paper["now"])
        _dump(dest / "equity.json", paper["equity"])
        _dump(dest / "fills.json", paper["fills"])
        books_meta.append({"id": "paper", "mode": "PAPER", "href": "books/paper/now.json"})

    live = _book(
        book_id="live",
        mode="LIVE",
        entries=_load_journal(data / "execution_log_live.jsonl"),
        health=_load_json(data / "node_health_live.json"),
        data_dir=data,
        config_path=LIVE_CFG,
        knobs=_sleeve_knobs(root, LIVE_CFG, knobs),
        dna_knobs=knobs,
        live=True,
    )
    if live:
        dest = out / "books" / "live"
        _dump(dest / "now.json", live["now"])
        _dump(dest / "equity.json", live["equity"])
        _dump(dest / "fills.json", live["fills"])
        books_meta.append({"id": "live", "mode": "LIVE", "href": "books/live/now.json"})

    ident = {
        "config_path": dna_rel,
        "strategy_id": knobs.get("strategy_id"),
        "knobs": knobs,
    }
    _dump(out / "identity.json", ident)

    runs_meta: list[dict[str, Any]] = []
    bt = root / "results" / "backtests"
    if bt.is_dir():
        for d in sorted(p for p in bt.iterdir() if p.is_dir()):
            rollup_path = d / "rollup.json"
            if not rollup_path.is_file():
                continue
            raw = _load_json(rollup_path)
            if not isinstance(raw, list) or not raw:
                continue
            sidecar = _load_json(d / "knobs.json")
            if isinstance(sidecar, dict) and sidecar:
                run_knobs = {k: v for k, v in sidecar.items() if not str(k).startswith("_")}
                run_cfg = str(sidecar.get("_config_path") or dna_rel)
            else:
                run_knobs = knobs
                run_cfg = dna_rel
            run = _summarize_rollup(raw, run_id=d.name, knobs=run_knobs, config_path=run_cfg)
            _dump(out / "runs" / f"{d.name}.json", run)
            runs_meta.append(
                {
                    "id": d.name,
                    "role": run["role"],
                    "n": run["n"],
                    "avg_return_pct": run["avg_return_pct"],
                    "href": f"runs/{d.name}.json",
                }
            )

    now = datetime.now(timezone.utc)
    index = {
        "as_of_utc": now.isoformat(),
        "as_of_et": format_et(now),
        "books": books_meta,
        "runs": runs_meta,
        "identity": "identity.json",
    }
    _dump(out / "index.json", index)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Project pro-trader journals into viz snapshots.")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    root = args.root
    out = args.out or (root / "viz" / "snapshots")
    project(root=root, out=out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
