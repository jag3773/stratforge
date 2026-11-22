"""
Trading node for trend-rotation 25 on Coinbase US (spot).

Modes:
  PAPER   — PaperExecutor, journal data/execution_log.jsonl (default; systemd)
  LIVE    — LiveExecutor real orders; requires LIVE_TRADING=true,
            SIMULATION_MODE=false, data/LIVE_APPROVED, COINBASE_PORTFOLIO_ID

Architecture:
  PAPER   — REST 5m fold → SignalEngine → apply_engine_bar → PaperExecutor
  LIVE    — REST 5m fold → SignalEngine → apply_engine_bar → LiveExecutor + ccxt_bridge

The old dry-run node mode was removed. LiveExecutor(dry_run=True) remains for tests.
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
from strategies.config_load import load_config
from strategies.signal_engine import SignalEngine, SignalConfig
from strategies.paper_executor import PaperExecutor
from strategies.live_executor import LiveExecutor
from strategies.universe import load_live_universe
from strategies.node_lock import NodeLock
from strategies.bar_freshness import bar_tradable
from strategies.bar_apply import apply_engine_bar
from risk_limits import limits_from_config, is_trading_halted
import ccxt_bridge
from stratforge.state import state_root

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = str(state_root())
DEFAULT_CFG = os.path.join(ROOT, "config", "live_paper.json")
# Mode-specific health paths. Paper keeps canonical names used by paper_status.
_NODE_HEALTH_BY_MODE = {
    "paper": os.path.join(ROOT, "data", "node_health.json"),
    "live": os.path.join(ROOT, "data", "node_health_live.json"),
}
_FEED_HEALTH_BY_MODE = {
    "paper": os.path.join(ROOT, "data", "feed_health.json"),
    "live": os.path.join(ROOT, "data", "feed_health_live.json"),
}
NODE_HEALTH = _NODE_HEALTH_BY_MODE["paper"]  # default; set in main() per mode

# Admitted universe SSOT: config/universe_live.json (never the watchlist).
UNIVERSE = load_live_universe()


def select_data_feed_cls(run_mode: str):
    """All modes use REST LiveDataFeed."""
    from strategies.live_data_feed import LiveDataFeed

    return LiveDataFeed


def make_feed(run_mode, symbols, on_4h_bar, health_path, **kwargs):
    """Construct the REST feed. Ignore leftover trading_strategy kwarg if passed."""
    cls = select_data_feed_cls(run_mode)
    kwargs.pop("trading_strategy", None)
    kwargs["health_path"] = health_path
    return cls(symbols, on_4h_bar, **kwargs)


def _data_feed_label(run_mode: str) -> str:
    return "direct_rest"


def _cfg_to_signal(cfg: dict) -> SignalConfig:
    return SignalConfig.from_dict(cfg)


def _write_node_health(payload: dict, path: str | None = None):
    target = path or NODE_HEALTH
    try:
        tmp = target + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, target)
    except Exception as e:
        print(f"[node] health write failed: {e!r}", flush=True)


def _resolve_mode(cli_mode: str | None) -> str:
    m = (cli_mode or os.getenv("NODE_MODE") or "paper").strip().lower()
    if m not in ("paper", "live"):
        raise SystemExit(f"Unknown mode {m!r}; use paper|live")
    return m


def _backend_name(cfg: dict) -> str:
    raw = str(cfg.get("execution_backend") or os.getenv("EXECUTION_BACKEND") or "ccxt").strip().lower()
    if raw in ("native_v2", "native", "v2", "nautilus"):
        raise ValueError(f"execution backend {raw!r} is not supported; ccxt only")
    return "ccxt"


def _exec_bridge(cfg: dict):
    return ccxt_bridge


def _build_executor(run_mode: str, cfg: dict):
    start_usd = float(cfg.get("start_usd", 1000.0))
    max_alloc = float(cfg.get("max_alloc_pct", 0.25))
    fee = float(cfg.get("fee_rate", 0.001))
    limits = limits_from_config(cfg)
    if not getattr(limits, "halt_mode", ""):
        limits.halt_mode = run_mode
    backend = _backend_name(cfg)

    if run_mode == "paper":
        journal = os.path.join(ROOT, "data", "execution_log.jsonl")
        return PaperExecutor(
            start_usd=start_usd,
            max_alloc_pct=max_alloc,
            journal_path=journal,
            fee_rate=fee,
            limits=limits,
        ), journal

    if run_mode != "live":
        raise SystemExit(f"Unknown mode {run_mode!r}; use paper|live")

    # LIVE — hard gates
    ccxt_bridge.load_live_secrets()
    sim = os.getenv("SIMULATION_MODE", "true").lower() != "false"
    live_flag = os.getenv("LIVE_TRADING", "false").lower() == "true"
    if sim or not live_flag:
        raise SystemExit(
            "LIVE mode refused: set LIVE_TRADING=true AND SIMULATION_MODE=false "
            "(and create data/LIVE_APPROVED). Aborting."
        )
    pid = (os.getenv("COINBASE_PORTFOLIO_ID") or "").strip()
    if not pid:
        raise SystemExit(
            "LIVE mode refused: COINBASE_PORTFOLIO_ID must be set to the funded "
            "sleeve portfolio (no silent default)."
        )
    bridge = _exec_bridge(cfg)
    st = bridge.status()
    if st["dry_run"]:
        raise SystemExit(
            f"LIVE mode refused by {backend} bridge: dry_run still true "
            f"(approval={st.get('approval_reason')}, has_keys={st.get('has_keys')})"
        )
    journal = os.path.join(ROOT, "data", "execution_log_live.jsonl")
    return (
        LiveExecutor(
            start_usd=start_usd,
            max_alloc_pct=max_alloc,
            journal_path=journal,
            fee_rate=fee,
            venue_taker_fee_rate=float(cfg.get("venue_taker_fee_rate", 0.012)),
            dry_run=False,
            limits=limits,
            mode_label="LIVE",
            backend=backend,
            buy_protect=cfg.get("buy_protect") or {},
        ),
        journal,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--symbols", default=None, help="comma-separated subset")
    ap.add_argument(
        "--mode",
        default=None,
        choices=["paper", "live"],
        help="paper (default) | live",
    )
    args = ap.parse_args()

    run_mode = _resolve_mode(args.mode)
    print(f"[node] requested_mode={run_mode}", flush=True)
    node_lock = NodeLock(run_mode)
    node_lock.acquire()
    node_health_path = _NODE_HEALTH_BY_MODE[run_mode]
    feed_health_path = _FEED_HEALTH_BY_MODE[run_mode]
    global NODE_HEALTH
    NODE_HEALTH = node_health_path
    print(f"[node] health node={node_health_path} feed={feed_health_path}", flush=True)
    print(f"[node] runtime python={sys.version.split()[0]}", flush=True)

    cfg = load_config(args.config)

    # Optional symbol allowlist from production risk config
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

    try:
        executor, journal = _build_executor(run_mode, cfg)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"executor init failed: {e}") from e

    sig_cfg = _cfg_to_signal(cfg)
    max_alloc = float(cfg.get("max_alloc_pct", 0.25))
    start_usd = float(cfg.get("start_usd", 1000.0))
    engine = SignalEngine(sig_cfg, symbols)
    strategy_id = cfg.get("strategy_id", "trend_rotation_25_max8_rotate")

    print(
        f"[node] mode={run_mode} journal={journal} "
        f"slow_ema_exit_underwater={sig_cfg.slow_ema_exit_underwater}",
        flush=True,
    )
    if run_mode != "paper":
        bridge = _exec_bridge(cfg)
        print(
            f"[node] backend={_backend_name(cfg)} bridge_status={bridge.status()}",
            flush=True,
        )
        allow = getattr(getattr(executor, "limits", None), "allowed_symbols", None)
        print(f"[node] risk_allowed_symbols={allow} (None=unrestricted)", flush=True)

    # Preserve paper/live book across restarts (strategy updates must not wipe ledger).
    if hasattr(executor, "restore_from_journal"):
        restored = executor.restore_from_journal()
        print(f"[node] journal_restore={restored}", flush=True)
        engine.sync_slots_from_positions(executor.positions)
        if run_mode == "live" and hasattr(executor, "apply_reconcile"):
            from strategies.live_reconcile import fetch_coinbase_book

            book = fetch_coinbase_book(os.getenv("COINBASE_PORTFOLIO_ID"))
            rec = executor.apply_reconcile(book)
            print(f"[node] reconcile={rec.as_dict()}", flush=True)
            if not rec.ok:
                print(
                    "[node] LIVE buys blocked until exchange book matches journal",
                    flush=True,
                )
        if run_mode == "paper" and hasattr(executor, "journal_meta"):
            executor.journal_meta(
                {
                    "action": "CONFIG_UPDATE",
                    "strategy_id": strategy_id,
                    "max_concurrent": sig_cfg.max_concurrent,
                    "max_alloc_pct": max_alloc,
                    "rotate_when_full": sig_cfg.rotate_when_full,
                    "rotate_min_hold_bars": sig_cfg.rotate_min_hold_bars,
                    "exit_mode": sig_cfg.exit_mode,
                    "atr_stop_mult": sig_cfg.atr_stop_mult,
                    "channel_period": sig_cfg.channel_period,
                    "signal_mode": sig_cfg.mode,
                    "note": "strategy params updated; balance/positions preserved",
                }
            )

    feed_seed = make_feed(
        run_mode, symbols, lambda s, b: None, feed_health_path
    )
    seed_bars = feed_seed.seed()
    warmed = 0
    for sym in symbols:
        bars = seed_bars.get(sym, [])
        for b in bars:
            engine.on_bar(sym, b, emit=False)
        warmed += len(bars)
        if bars:
            executor.update_price(sym, bars[-1]["close"])
    # Re-sync slots after warm in case restore ran first.
    engine.sync_slots_from_positions(executor.positions)
    halted, why = is_trading_halted(getattr(executor, "limits", None))
    if halted:
        print(f"[node] kill switch engaged at start: {why}", flush=True)
        executor.apply([])
        engine.sync_slots_from_positions(executor.positions)
        print(
            f"[node] halt_flatten n_positions={len(executor.positions)} "
            f"cash={executor.cash:.2f} equity={executor.equity():.2f}",
            flush=True,
        )
    print(
        f"[node] warmed indicators with {warmed} historical 4h bars "
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

    def on_4h_bar(symbol: str, bar: dict):
        nonlocal bars_seen, actions_seen
        price = float(bar["close"])
        try:
            with lock:
                if run_mode == "live" and hasattr(executor, "sync_exchange_book"):
                    rec = executor.sync_exchange_book()
                    if rec is not None:
                        print(f"[node] venue_sync={rec.as_dict()}", flush=True)
                tradable, why = bar_tradable(bar)
                if not tradable:
                    print(
                        f"[node] skip_apply {symbol} ({why})",
                        flush=True,
                    )
                actions = apply_engine_bar(
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
                # Buy-and-hold BTC benchmark uses ORIGINAL paper start, not current cash.
                btc_ref = float(cfg.get("start_usd", 1000.0))
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
                    f"actions={len(actions)} mode={run_mode}",
                    flush=True,
                )
                _write_node_health(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "started_at": started_at,
                        "mode": run_mode,
                        "strategy": strategy_id,
                        "bars_seen": bars_seen,
                        "actions_seen": actions_seen,
                        "last_bar_symbol": symbol,
                        "last_bar_close": price,
                        "equity": eq,
                        "cash": snap["cash"],
                        "trades": snap["trades"],
                        "n_positions": npos,
                        "positions": sorted(snap.get("positions", {}).keys()),
                        "max_concurrent": sig_cfg.max_concurrent,
                        "rotate_when_full": sig_cfg.rotate_when_full,
                        "rotate_min_hold_bars": sig_cfg.rotate_min_hold_bars,
                        "btc_start": btc_start,
                        "btc_now": btc_now,
                        "data_feed": _data_feed_label(run_mode),
                        "dry_run": snap.get("dry_run", run_mode != "live"),
                    },
                    path=node_health_path,
                )
        except Exception:
            print("[node] FATAL in on_4h_bar:\n" + traceback.format_exc(), flush=True)
            raise

    live = make_feed(
        run_mode,
        symbols,
        on_4h_bar,
        feed_health_path,
        history_end_iso=None,
    )
    live.init_emitted_from_seed(seed_bars)

    print(
        f"[node] started mode={run_mode} symbols={len(symbols)} "
        f"strategy={strategy_id} "
        f"mode={sig_cfg.mode} alloc={max_alloc} "
        f"exit={sig_cfg.exit_mode} channel={sig_cfg.channel_period} "
        f"max_concurrent={sig_cfg.max_concurrent} "
        f"rotate_when_full={sig_cfg.rotate_when_full} "
        f"rotate_min_hold_bars={sig_cfg.rotate_min_hold_bars}",
        flush=True,
    )
    _write_node_health(
        {
            "ts": started_at,
            "started_at": started_at,
            "mode": run_mode,
            "strategy": strategy_id,
            "bars_seen": 0,
            "status": "starting",
            "symbols": len(symbols),
            "max_concurrent": sig_cfg.max_concurrent,
            "rotate_when_full": sig_cfg.rotate_when_full,
            "rotate_min_hold_bars": sig_cfg.rotate_min_hold_bars,
            "cash": round(float(executor.cash), 2),
            "equity": round(float(executor.equity()), 2),
            "n_positions": len(executor.positions),
            "positions": sorted(executor.positions.keys()),
            "data_source": cfg.get("data_source"),
            "data_feed": _data_feed_label(run_mode),
        },
        path=node_health_path,
    )
    try:
        live.run()
    except KeyboardInterrupt:
        print("[node] stopped by user", flush=True)
    finally:
        live.stop()


if __name__ == "__main__":
    main()
