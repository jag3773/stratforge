"""Isolated paper soak node for research families (donch_d20_10).

Never imported by live_node. Never writes SSOT paper journal/health.
PAPER ONLY — refuses LIVE_TRADING=true.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk_limits import is_trading_halted, limits_from_config
from strategies.bar_apply import apply_family_bar
from strategies.bar_freshness import bar_tradable
from strategies.config_load import load_config
from strategies.daily_data_feed import BAR_MINUTES, DailyDataFeed
from strategies.node_lock import NodeLock
from strategies.paper_executor import PaperExecutor
from strategies.research_families import FamilyEngine
from strategies.universe import load_live_universe

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_CFG = os.path.join(ROOT, "config", "live_paper_donch.json")
UNIVERSE = load_live_universe()
# Daily close + 50-symbol slow poll: allow 45 min lag vs 4h paper's 20 min.
BAR_MAX_LAG_SEC = float(os.getenv("DONCH_BAR_MAX_LAG_SEC", str(45 * 60)))


def _write_json(path: str, payload: dict) -> None:
    try:
        tmp = path + ".tmp"
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[node] health write failed: {e!r}", flush=True)


def _abs_data_path(rel: str) -> str:
    p = Path(rel)
    if p.is_absolute():
        return str(p)
    return str(Path(ROOT) / p)


def main() -> None:
    if os.getenv("LIVE_TRADING", "false").lower() == "true":
        raise SystemExit("paper_family_node refuses LIVE_TRADING=true")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    strategy_id = str(cfg.get("strategy_id") or cfg.get("family") or "donch_d20_10")
    lock_mode = str(cfg.get("lock_mode") or "paper_donch")
    journal = _abs_data_path(str(cfg.get("journal_path") or "data/execution_log_donch.jsonl"))
    node_health_path = _abs_data_path(str(cfg.get("node_health_path") or "data/node_health_donch.json"))
    feed_health_path = _abs_data_path(str(cfg.get("feed_health_path") or "data/feed_health_donch.json"))
    if "execution_log.jsonl" == os.path.basename(journal) and "donch" not in journal:
        raise SystemExit("refusing to use SSOT paper journal")
    if os.path.basename(node_health_path) == "node_health.json":
        raise SystemExit("refusing to use SSOT node_health.json")
    if os.path.basename(feed_health_path) == "feed_health.json":
        raise SystemExit("refusing to use SSOT feed_health.json")

    node_lock = NodeLock(lock_mode)
    node_lock.acquire()
    print(f"[node] requested_mode=paper_family strategy={strategy_id}", flush=True)
    print(f"[node] health node={node_health_path} feed={feed_health_path}", flush=True)
    print(f"[node] runtime python={sys.version.split()[0]}", flush=True)

    risk_syms = (cfg.get("risk") or {}).get("allowed_symbols")
    default_syms = risk_syms if risk_syms else UNIVERSE
    symbols = [
        s.strip().upper()
        for s in (args.symbols or ",".join(default_syms)).split(",")
        if s.strip()
    ]
    symbols = [s for s in symbols if s in UNIVERSE]
    if not symbols:
        raise SystemExit("No valid symbols")

    start_usd = float(cfg.get("start_usd", 500.0))
    max_alloc = float(cfg.get("max_alloc_pct", 0.25))
    fee = float(cfg.get("fee_rate", 0.001))
    limits = limits_from_config(cfg)
    write_opening = not os.path.isfile(journal)
    executor = PaperExecutor(
        start_usd=start_usd,
        max_alloc_pct=max_alloc,
        journal_path=journal,
        fee_rate=fee,
        write_opening=write_opening,
        limits=limits,
    )
    print(f"[node] mode=paper_family journal={journal} write_opening={write_opening}", flush=True)

    fam_cfg = {
        "family": str(cfg.get("family") or strategy_id),
        "bar_minutes": int(cfg.get("bar_minutes") or BAR_MINUTES),
        "entry_n": int(cfg.get("entry_n") or 20),
        "exit_n": int(cfg.get("exit_n") or 10),
        "max_concurrent": int(cfg.get("max_concurrent") or 8),
        "max_alloc_pct": max_alloc,
        "rotate_when_full": bool(cfg.get("rotate_when_full", True)),
        "rotate_min_hold_bars": int(cfg.get("rotate_min_hold_bars") or 3),
    }
    engine = FamilyEngine(fam_cfg, symbols)

    if hasattr(executor, "restore_from_journal"):
        restored = executor.restore_from_journal()
        print(f"[node] journal_restore={restored}", flush=True)
        engine.sync_from_positions(executor.positions)
        if hasattr(executor, "journal_meta"):
            executor.journal_meta(
                {
                    "action": "CONFIG_UPDATE",
                    "strategy_id": strategy_id,
                    "family": fam_cfg["family"],
                    "bar_minutes": fam_cfg["bar_minutes"],
                    "entry_n": fam_cfg["entry_n"],
                    "exit_n": fam_cfg["exit_n"],
                    "max_concurrent": fam_cfg["max_concurrent"],
                    "max_alloc_pct": max_alloc,
                    "rotate_when_full": fam_cfg["rotate_when_full"],
                    "rotate_min_hold_bars": fam_cfg["rotate_min_hold_bars"],
                    "atr_stop_mult": float(cfg.get("atr_stop_mult") or 0.0),
                    "exit_mode": cfg.get("exit_mode"),
                    "note": "donch paper soak; SSOT paper book untouched",
                }
            )

    feed = DailyDataFeed(symbols, lambda s, b: None, health_path=feed_health_path)
    seed_bars = feed.seed()
    warmed = 0
    for sym in symbols:
        bars = seed_bars.get(sym, [])
        for b in bars:
            engine.update(sym, b)
        warmed += len(bars)
        if bars:
            executor.update_price(sym, bars[-1]["close"])
    engine.sync_from_positions(executor.positions)
    halted, why = is_trading_halted(getattr(executor, "limits", None))
    if halted:
        print(f"[node] kill switch engaged at start: {why}", flush=True)
        executor.apply([])
        engine.sync_from_positions(executor.positions)
        print(
            f"[node] halt_flatten n_positions={len(executor.positions)} "
            f"cash={executor.cash:.2f} equity={executor.equity():.2f}",
            flush=True,
        )
    print(
        f"[node] warmed FamilyEngine with {warmed} historical 1d bars "
        f"across {len(symbols)} symbols | open={sorted(executor.positions.keys())} "
        f"cash={executor.cash:.2f} equity={executor.equity():.2f}",
        flush=True,
    )

    btc_start = executor.last_price.get("BTC")
    print(f"[node] BTC reference price @ start: {btc_start}", flush=True)
    started_at = datetime.now(timezone.utc).isoformat()
    bars_seen = 0
    actions_seen = 0
    lock = threading.RLock()

    def on_bar(symbol: str, bar: dict) -> None:
        nonlocal bars_seen, actions_seen
        price = float(bar["close"])
        try:
            with lock:
                tradable, why_bar = bar_tradable(
                    bar,
                    max_lag_sec=BAR_MAX_LAG_SEC,
                    bar_minutes=fam_cfg["bar_minutes"],
                )
                if not tradable:
                    print(f"[node] skip_apply {symbol} ({why_bar})", flush=True)
                actions = apply_family_bar(
                    engine, executor, symbol, bar, apply_enabled=tradable
                )
                bars_seen += 1
                if actions:
                    actions_seen += len(actions)
                    for a in actions:
                        px = float(a.get("price") or price)
                        print(
                            f"[node] {a['type']} {a['symbol']} @ {px:.6g} "
                            f"({a.get('reason')}) | equity={executor.equity():.2f}",
                            flush=True,
                        )
                snap = executor.snapshot()
                eq = snap["equity"]
                btc_ref = float(cfg.get("start_usd", 500.0))
                btc_now = executor.last_price.get("BTC")
                if btc_start and btc_now:
                    btc_str = f"{btc_ref * (btc_now / btc_start):.2f}"
                else:
                    btc_str = "n/a"
                npos = snap.get("n_positions", 0)
                if isinstance(snap.get("positions"), dict) and not isinstance(npos, int):
                    npos = len(snap["positions"])
                print(
                    f"[node] bar={symbol} close={price:.6g} equity=${eq:.2f} "
                    f"BTC_buyhold=${btc_str} pos={npos} trades={snap['trades']} "
                    f"actions={len(actions)} mode=paper_family",
                    flush=True,
                )
                _write_json(
                    node_health_path,
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "started_at": started_at,
                        "mode": "paper_family",
                        "strategy": strategy_id,
                        "family": fam_cfg["family"],
                        "bar_minutes": fam_cfg["bar_minutes"],
                        "entry_n": fam_cfg["entry_n"],
                        "exit_n": fam_cfg["exit_n"],
                        "bars_seen": bars_seen,
                        "actions_seen": actions_seen,
                        "last_bar_symbol": symbol,
                        "last_bar_close": price,
                        "equity": eq,
                        "cash": snap["cash"],
                        "trades": snap["trades"],
                        "n_positions": npos,
                        "positions": sorted(snap.get("positions", {}).keys()),
                        "max_concurrent": fam_cfg["max_concurrent"],
                        "rotate_when_full": fam_cfg["rotate_when_full"],
                        "rotate_min_hold_bars": fam_cfg["rotate_min_hold_bars"],
                        "btc_start": btc_start,
                        "btc_now": btc_now,
                        "data_feed": "direct_rest_1d",
                        "dry_run": True,
                    },
                )
        except Exception:
            print("[node] FATAL in on_bar:\n" + traceback.format_exc(), flush=True)
            raise

    live = DailyDataFeed(
        symbols,
        on_bar,
        history_end_iso=None,
        health_path=feed_health_path,
    )
    live.init_emitted_from_seed(seed_bars)

    print(
        f"[node] started mode=paper_family symbols={len(symbols)} "
        f"strategy={strategy_id} family={fam_cfg['family']} "
        f"bar_minutes={fam_cfg['bar_minutes']} entry_n={fam_cfg['entry_n']} "
        f"exit_n={fam_cfg['exit_n']} alloc={max_alloc} "
        f"max_concurrent={fam_cfg['max_concurrent']} "
        f"rotate_when_full={fam_cfg['rotate_when_full']} "
        f"rotate_min_hold_bars={fam_cfg['rotate_min_hold_bars']}",
        flush=True,
    )
    _write_json(
        node_health_path,
        {
            "ts": started_at,
            "started_at": started_at,
            "mode": "paper_family",
            "strategy": strategy_id,
            "family": fam_cfg["family"],
            "bar_minutes": fam_cfg["bar_minutes"],
            "status": "starting",
            "symbols": len(symbols),
            "max_concurrent": fam_cfg["max_concurrent"],
            "rotate_when_full": fam_cfg["rotate_when_full"],
            "rotate_min_hold_bars": fam_cfg["rotate_min_hold_bars"],
            "cash": round(float(executor.cash), 2),
            "equity": round(float(executor.equity()), 2),
            "n_positions": len(executor.positions),
            "positions": sorted(executor.positions.keys()),
            "data_source": cfg.get("data_source"),
            "data_feed": "direct_rest_1d",
        },
    )
    try:
        live.run()
    except KeyboardInterrupt:
        print("[node] stopped by user", flush=True)
    finally:
        live.stop()


if __name__ == "__main__":
    main()
