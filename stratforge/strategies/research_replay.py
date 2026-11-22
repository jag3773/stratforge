"""
Canonical research / backtest path for nautilus-bot (Nautilus 2.x / .venv).

Replays multi-symbol 4h bars through:
  SignalEngine (decision SSOT) + PaperExecutor (CASH spot fills)

This is decision-faithful to live paper. It does **not** use the legacy
Nautilus 1.221 BacktestEngine / Strategy wrapper (archived).

Native Nautilus 2.x BacktestEngine exists in the wheel, but the Python Strategy
API surface changed; production DNA stays on SignalEngine until a dedicated
v2 Strategy adapter is written and parity-tested.

Usage:
  from strategies.research_replay import run_portfolio_replay, load_strategy_cfg
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.paper_executor import PaperExecutor  # noqa: E402
from strategies.signal_engine import SignalConfig, SignalEngine  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
DEFAULT_STRATEGY = ROOT / "config" / "trend_rotation_25_max8_rotate.json"
BASELINE_STRATEGY = ROOT / "config" / "trend_rotation_25pct.json"


def load_strategy_cfg(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_STRATEGY
    if not p.is_absolute():
        p = ROOT / p
    from strategies.config_load import load_config

    raw = load_config(p)
    cfg: dict[str, Any] = {}
    for k, v in raw.items():
        if k.startswith("_") or k in ("comment", "benchmarks_note", "risk"):
            continue
        if k.startswith("results_") or k.startswith("benchmarks"):
            continue
        cfg[k] = v
    cfg["_config_path"] = str(p)
    return cfg


def _to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _resample_rule(bar_minutes: int) -> str:
    """Pandas offset. Keep literal '4h' for SSOT 240 so existing cohorts stay bit-identical."""
    bm = int(bar_minutes or 240)
    if bm == 240:
        return "4h"
    if bm % 60 == 0:
        return f"{bm // 60}h"
    return f"{bm}min"


def load_4h_bars(
    symbol: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    raw_dir: Path = RAW_DIR,
    bar_minutes: int = 240,
) -> list[dict]:
    path = raw_dir / f"{symbol.upper()}_USD_5m.parquet"
    if not path.is_file():
        return []
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        # Corrupted / truncated download — skip symbol rather than kill the portfolio run
        print(f"[research_replay] skip {symbol}: cannot read {path.name}: {type(exc).__name__}: {exc}", flush=True)
        return []
    if df.empty:
        return []
    df = df.copy()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    if isinstance(start, str):
        start_dt = _to_dt(start)
    else:
        start_dt = start
    if isinstance(end, str):
        end_dt = _to_dt(end)
        if end_dt is not None:
            # include full end calendar day
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
    else:
        end_dt = end
    if start_dt is not None:
        df = df[df["ts"] >= start_dt]
    if end_dt is not None:
        df = df[df["ts"] <= end_dt]
    if df.empty:
        return []
    df = df.set_index("ts").sort_index()
    rule = _resample_rule(bar_minutes)
    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    l = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    v = df["volume"].resample(rule).sum()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    bars: list[dict] = []
    for t, row in out.iterrows():
        bars.append(
            {
                "ts": int(t.timestamp() * 1_000_000_000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return bars


def cfg_to_signal(cfg: dict) -> SignalConfig:
    return SignalConfig.from_dict(cfg)


def warmup_start(trade_start: datetime, cfg: dict) -> datetime:
    """Load enough pre-start 4h bars for slow EMA / channel to initialize."""
    bar_minutes = int(cfg.get("bar_minutes", 240) or 240)
    slow = int(cfg.get("slow_ema_period", 200) or 200)
    channel = int(cfg.get("channel_period", 60) or 60)
    need_bars = max(slow, channel, 50) + 5
    need_hours = need_bars * bar_minutes / 60.0
    return trade_start - timedelta(hours=need_hours, days=3)


# Score-based ranks must update the whole ts group before emit (need peeks).
# Order-only ranks (Z–A, fixed perm) stay on the stream path like A–Z so
# CLOSE timing is not coupled to entry order — otherwise closed-trade count
# collapses and the control is invalid.
SCORE_RANKS = {
    "breakout_pct",
    "breakout_atr",
    "breakout_least",
}
ORDER_RANKS = {
    "symbol_desc",
    "symbol_perm",
}
GROUPED_RANKS = SCORE_RANKS | ORDER_RANKS  # apply_bar_group still accepts both


def parse_rank_entries(rank_entries: str) -> tuple[str, int]:
    """Split 'symbol_perm:7' → ('symbol_perm', 7). Other modes seed=0."""
    raw = str(rank_entries or "").strip()
    if not raw:
        return "", 0
    if raw.startswith("symbol_perm"):
        parts = raw.split(":", 1)
        seed = 1
        if len(parts) == 2 and str(parts[1]).strip():
            seed = int(parts[1])
        return "symbol_perm", seed
    return raw, 0


def symbol_perm_key(symbol: str, seed: int) -> str:
    return hashlib.sha256(f"{int(seed)}:{symbol}".encode()).hexdigest()


def group_sort_key(engine, rank: str, seed: int, sb: tuple[str, dict]):
    """Sort key for same-ts emit order. Lower sorts first."""
    sym = sb[0]
    if rank == "symbol_desc":
        return (0, _invert_symbol(sym))
    if rank == "symbol_perm":
        return (symbol_perm_key(sym, seed), sym)
    if rank == "breakout_least":
        sc = engine.peek_breakout_pct(sym)
        val = sc if sc is not None else float("inf")
        return (val, sym)
    if rank in {"breakout_pct", "breakout_atr"}:
        sc = engine.peek_entry_score(sym, rank)
        val = -(sc if sc is not None else float("-inf"))
        return (val, sym)
    return (0, sym)


def _invert_symbol(sym: str) -> str:
    """Lexicographic reverse: ZZZ < AAA when sorting ascending."""
    return "".join(chr(0x10FFFF - ord(c)) for c in sym)


def apply_bar_group(
    engine,
    group: list[tuple[str, dict]],
    positions: dict | None,
    rank_entries: str = "",
    apply_fn=None,
) -> list[dict]:
    """Update a same-timestamp group, then emit. Optional research reorder.

    Live/paper keep calling on_bar per symbol (FCFS / A–Z). Research may set
    rank_entries so a different same-bar order takes leftover slots.
    """
    applied: list[dict] = []
    rank, seed = parse_rank_entries(rank_entries)
    if rank not in GROUPED_RANKS:
        for sym, bar in group:
            acts = engine.on_bar(sym, bar, emit=True, positions=positions)
            applied.extend(acts)
            if apply_fn and acts:
                apply_fn(acts, sym, bar)
        return applied

    for sym, bar in group:
        engine.on_bar(sym, bar, emit=False, positions=None)

    ordered = sorted(group, key=lambda sb: group_sort_key(engine, rank, seed, sb))
    for sym, bar in ordered:
        acts = engine.decide(sym, bar, positions=positions)
        applied.extend(acts)
        if apply_fn and acts:
            apply_fn(acts, sym, bar)
            if positions is not None:
                engine.sync_slots_from_positions(positions)
    return applied


def _max_drawdown_pct(curve: list[tuple[int, float]]) -> float:
    if not curve:
        return 0.0
    peak = curve[0][1]
    max_dd = 0.0
    for _, eq in curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return float(max_dd)


def _active_months(curve: list[tuple[int, float]], trade_months: set[str]) -> int:
    if trade_months:
        return len(trade_months)
    months: set[str] = set()
    for ts, _ in curve:
        if ts <= 0:
            continue
        dt = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
        months.add(f"{dt.year:04d}-{dt.month:02d}")
    return len(months)


@dataclass
class ReplayResult:
    symbols: list[str]
    start_usd: float
    end_equity: float
    end_cash: float
    trades: int
    wins: int
    losses: int
    max_dd_pct: float
    net_return_pct: float
    expectancy_usd: float
    open_positions: int
    bars: int
    first_ts: int | None
    last_ts: int | None
    active_months: int
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    # Extended (stagger / portfolio reports)
    positions: list[dict] = field(default_factory=list)
    positions_mv: float = 0.0
    positions_cost: float = 0.0
    closed_trades: list[dict] = field(default_factory=list)
    trade_events: list[dict] = field(default_factory=list)
    first_trade_ts: int | None = None
    last_trade_ts: int | None = None
    open_max: int = 0
    config: dict = field(default_factory=dict)
    config_path: str | None = None
    fee_rate: float = 0.001
    data_start_warmup: str | None = None
    trade_start: str | None = None
    trade_end: str | None = None
    engine: str = "signal_engine_cash_replay"
    account_type: str = "CASH"

    def to_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "start_usd": self.start_usd,
            "end_equity": self.end_equity,
            "end_cash": self.end_cash,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "max_dd_pct": self.max_dd_pct,
            "net_return_pct": self.net_return_pct,
            "expectancy_usd": self.expectancy_usd,
            "open_positions": self.open_positions,
            "bars": self.bars,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "active_months": self.active_months,
            "positions_mv": self.positions_mv,
            "positions_cost": self.positions_cost,
            "open_max": self.open_max,
            "first_trade_ts": self.first_trade_ts,
            "last_trade_ts": self.last_trade_ts,
            "fee_rate": self.fee_rate,
            "engine": self.engine,
            "account_type": self.account_type,
            "config_path": self.config_path,
            "data_start_warmup": self.data_start_warmup,
            "trade_start": self.trade_start,
            "trade_end": self.trade_end,
        }


def run_portfolio_replay(
    symbols: Iterable[str],
    *,
    strategy_cfg: dict | None = None,
    config_path: str | Path | None = None,
    start: str | None = None,
    end: str | None = None,
    start_usd: float = 1000.0,
    fee_rate: float = 0.001,
    trade_start_ns: int | None = None,
    raw_dir: Path | None = None,
    bars_by_symbol: dict[str, list[dict]] | None = None,
) -> ReplayResult:
    """
    Replay SSOT SignalEngine on CASH PaperExecutor over shared multi-symbol 4h stream.

    If trade_start_ns is set, bars before that timestamp warm indicators only
    (no entries/exits). When start is given without trade_start_ns, start is both
    data filter and trade gate unless you pass an earlier data window via start
    and an explicit trade_start_ns.
    """
    cfg = strategy_cfg if strategy_cfg is not None else load_strategy_cfg(config_path)
    raw = Path(raw_dir) if raw_dir else RAW_DIR
    start_usd = float(start_usd)
    fee_rate = float(fee_rate)

    # Capital window for reporting; optional indicator warmup before trade gate
    trade_start_dt = _to_dt(start) if start else None
    trade_end_dt = _to_dt(end) if end else None
    if trade_end_dt is not None:
        trade_end_dt = trade_end_dt.replace(hour=23, minute=59, second=59)

    if trade_start_ns is None and trade_start_dt is not None:
        trade_start_ns = int(trade_start_dt.timestamp() * 1_000_000_000)

    data_start_dt = trade_start_dt
    if trade_start_dt is not None and trade_start_ns is not None:
        # Always warm indicators when we have a capital start
        data_start_dt = warmup_start(trade_start_dt, cfg)

    syms = [s.upper() for s in symbols if str(s).strip()]
    per: dict[str, list[dict]] = {}
    if bars_by_symbol is not None:
        start_ns = int(data_start_dt.timestamp() * 1_000_000_000) if data_start_dt else None
        end_ns = int(trade_end_dt.timestamp() * 1_000_000_000) if trade_end_dt else None
        for s in syms:
            src = bars_by_symbol.get(s) or bars_by_symbol.get(s.lower())
            if not src:
                continue
            bars = src
            if start_ns is not None or end_ns is not None:
                bars = [
                    b
                    for b in src
                    if (start_ns is None or int(b["ts"]) >= start_ns)
                    and (end_ns is None or int(b["ts"]) <= end_ns)
                ]
            if bars:
                per[s] = bars
    else:
        bm = int(cfg.get("bar_minutes", 240) or 240)
        for s in syms:
            bars = load_4h_bars(
                s,
                start=data_start_dt,
                end=trade_end_dt,
                raw_dir=raw,
                bar_minutes=bm,
            )
            if bars:
                per[s] = bars

    empty = ReplayResult(
        symbols=syms,
        start_usd=start_usd,
        end_equity=start_usd,
        end_cash=start_usd,
        trades=0,
        wins=0,
        losses=0,
        max_dd_pct=0.0,
        net_return_pct=0.0,
        expectancy_usd=0.0,
        open_positions=0,
        bars=0,
        first_ts=None,
        last_ts=None,
        active_months=0,
        config=dict(cfg),
        config_path=cfg.get("_config_path"),
        fee_rate=fee_rate,
        data_start_warmup=data_start_dt.date().isoformat() if data_start_dt else None,
        trade_start=start,
        trade_end=end,
    )
    if not per:
        return empty

    stream: list[tuple[int, str, dict]] = []
    for s, bars in per.items():
        for b in bars:
            stream.append((int(b["ts"]), s, b))
    rank_mode, rank_seed = parse_rank_entries(str(cfg.get("rank_entries") or ""))
    if rank_mode == "symbol_desc":
        stream.sort(key=lambda x: (x[0], _invert_symbol(x[1])))
    elif rank_mode == "symbol_perm":
        stream.sort(key=lambda x: (x[0], symbol_perm_key(x[1], rank_seed), x[1]))
    else:
        stream.sort(key=lambda x: (x[0], x[1]))

    sig_cfg = cfg_to_signal(cfg)
    engine = SignalEngine(sig_cfg, list(per.keys()))
    fd, journal = tempfile.mkstemp(prefix="research_replay_", suffix=".jsonl")
    os.close(fd)
    try:
        ex = PaperExecutor(
            start_usd=start_usd,
            max_alloc_pct=float(cfg.get("max_alloc_pct", 0.25)),
            journal_path=journal,
            fee_rate=fee_rate,
            write_opening=False,
        )
        curve: list[tuple[int, float]] = []
        trade_months: set[str] = set()
        trade_events: list[dict] = []
        closed_trades: list[dict] = []
        n_bars = 0
        open_max = 0
        first_trade_ts: int | None = None
        last_trade_ts: int | None = None
        # Track entries for closed-trade pairing
        entry_meta: dict[str, dict] = {}
        rank_entries = str(cfg.get("rank_entries") or "").strip()

        def _consume(actions: list[dict], ts: int, default_sym: str, px: float) -> None:
            nonlocal first_trade_ts, last_trade_ts, open_max
            prepared = [{**a, "price": a.get("price", px)} for a in actions]
            before = {s: dict(p) for s, p in ex.positions.items()}
            before_trades = ex.trades
            ex.apply(prepared)
            engine.sync_slots_from_positions(ex.positions)
            for a in prepared:
                act = str(a.get("type") or a.get("action") or "").upper()
                asym = str(a.get("symbol", default_sym)).upper()
                apx = float(a.get("price", px))
                if act == "BUY":
                    pos = ex.positions.get(asym)
                    if not pos:
                        continue
                    qty = float(pos["qty"])
                    entry = float(pos["entry"])
                    trade_events.append(
                        {
                            "action": "BUY",
                            "symbol": asym,
                            "price": entry,
                            "qty": qty,
                            "ts": ts,
                            "reason": a.get("reason"),
                            "score": a.get("score"),
                        }
                    )
                    entry_meta[asym] = {
                        "entry": entry,
                        "qty": qty,
                        "ts": pos.get("ts", ts),
                    }
                    if first_trade_ts is None:
                        first_trade_ts = ts
                    last_trade_ts = ts
                elif act in ("CLOSE", "SELL"):
                    prev = before.get(asym) or entry_meta.get(asym)
                    if not prev:
                        continue
                    qty = float(prev.get("qty") or 0.0)
                    entry = float(prev.get("entry") or apx)
                    entry_fee = float(prev.get("fee") or 0.0)
                    exit_fee = qty * apx * fee_rate
                    pnl = (apx - entry) * qty - exit_fee - entry_fee
                    trade_events.append(
                        {
                            "action": "CLOSE",
                            "symbol": asym,
                            "price": apx,
                            "qty": qty,
                            "pnl": pnl,
                            "ts": ts,
                            "reason": a.get("reason"),
                        }
                    )
                    closed_trades.append(
                        {
                            "symbol": asym,
                            "qty": qty,
                            "entry_price": entry,
                            "exit_price": apx,
                            "realized_pnl": pnl,
                            "ts_opened": prev.get("ts"),
                            "ts_closed": ts,
                            "open": False,
                        }
                    )
                    entry_meta.pop(asym, None)
                    if first_trade_ts is None:
                        first_trade_ts = ts
                    last_trade_ts = ts
            if ex.trades > before_trades:
                dt = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                trade_months.add(f"{dt.year:04d}-{dt.month:02d}")

        if parse_rank_entries(rank_entries)[0] in SCORE_RANKS:
            for ts, grp in groupby(stream, key=lambda r: r[0]):
                items = [(s, b) for _, s, b in grp]
                emit = True
                if trade_start_ns is not None and ts < int(trade_start_ns):
                    emit = False
                for s, b in items:
                    ex.update_price(s, float(b["close"]))
                    n_bars += 1
                if not emit:
                    for s, b in items:
                        engine.on_bar(s, b, emit=False)
                    continue

                def _apply(acts, sym, bar, ts=ts):
                    _consume(acts, ts, sym, float(bar["close"]))

                apply_bar_group(
                    engine,
                    items,
                    ex.positions,
                    rank_entries=rank_entries,
                    apply_fn=_apply,
                )
                open_max = max(open_max, len(ex.positions))
                curve.append((ts, ex.equity()))
        else:
            for ts, sym, bar in stream:
                emit = True
                if trade_start_ns is not None and ts < int(trade_start_ns):
                    emit = False
                px = float(bar["close"])
                ex.update_price(sym, px)
                actions = engine.on_bar(sym, bar, emit=emit, positions=ex.positions if emit else None)
                n_bars += 1
                if emit and actions:
                    _consume(actions, ts, sym, px)
                if emit:
                    open_max = max(open_max, len(ex.positions))
                    curve.append((ts, ex.equity()))

        # Open positions snapshot
        positions: list[dict] = []
        positions_mv = 0.0
        positions_cost = 0.0
        for s, p in ex.positions.items():
            qty = float(p.get("qty") or 0)
            entry = float(p.get("entry") or p.get("entry_price") or 0)
            last = float(ex.last_price.get(s, entry))
            mv = qty * last
            cost = qty * entry
            positions_mv += mv
            positions_cost += cost
            positions.append(
                {
                    "symbol": s,
                    "qty": qty,
                    "entry_price": entry,
                    "last_price": last,
                    "market_value": round(mv, 4),
                    "unrealized_pnl": round(mv - cost, 4),
                    "ts_opened": p.get("ts"),
                    "open": True,
                }
            )

        end_eq = ex.equity()
        net = (end_eq / start_usd - 1.0) if start_usd else 0.0
        exp = (end_eq - start_usd) / ex.trades if ex.trades > 0 else 0.0

        return ReplayResult(
            symbols=list(per.keys()),
            start_usd=start_usd,
            end_equity=end_eq,
            end_cash=float(ex.cash),
            trades=int(ex.trades),
            wins=int(ex.wins),
            losses=int(ex.losses),
            max_dd_pct=_max_drawdown_pct(curve),
            net_return_pct=float(net),
            expectancy_usd=float(exp),
            open_positions=len(ex.positions),
            bars=n_bars,
            first_ts=stream[0][0] if stream else None,
            last_ts=stream[-1][0] if stream else None,
            active_months=_active_months(curve, trade_months),
            equity_curve=curve,
            positions=positions,
            positions_mv=positions_mv,
            positions_cost=positions_cost,
            closed_trades=closed_trades,
            trade_events=trade_events,
            first_trade_ts=first_trade_ts,
            last_trade_ts=last_trade_ts,
            open_max=open_max,
            config=dict(cfg),
            config_path=cfg.get("_config_path"),
            fee_rate=fee_rate,
            data_start_warmup=data_start_dt.date().isoformat() if data_start_dt else None,
            trade_start=start,
            trade_end=end,
        )
    finally:
        try:
            os.unlink(journal)
        except OSError:
            pass


def btc_buyhold_return(start: str | None = None, end: str | None = None) -> float | None:
    bars = load_4h_bars("BTC", start=start, end=end)
    if len(bars) < 2:
        return None
    # If warmup not applied here, first/last are capital window when start/end set
    a = float(bars[0]["close"])
    b = float(bars[-1]["close"])
    if a <= 0:
        return None
    return b / a - 1.0


def btc_buyhold_end(start_usd: float, start: str | None, end: str | None) -> tuple[float, float, float | None, float | None]:
    """Return (end_value, return_pct, first_close, last_close) over capital window."""
    bars = load_4h_bars("BTC", start=start, end=end)
    if len(bars) < 2:
        return float(start_usd), 0.0, None, None
    a = float(bars[0]["close"])
    b = float(bars[-1]["close"])
    if a <= 0:
        return float(start_usd), 0.0, a, b
    ret = b / a - 1.0
    return float(start_usd) * (b / a), ret * 100.0, a, b
