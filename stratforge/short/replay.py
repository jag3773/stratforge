"""
Isolated 4h replay for the short stack.

Loads Coinbase spot 5m parquets as a CFM perp *price proxy*. Does not import
strategies.research_replay / SignalEngine / PaperExecutor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from short.engine import BearConfig, BearRegimeEngine
from short.ledger import ShortLedger
from stratforge.state import state_root


def default_raw_dir() -> Path:
    return state_root() / "data" / "raw"


def default_cfg() -> Path:
    return state_root() / "config" / "short" / "bear_regime_cover.json"


def load_cfg(path: str | Path | None = None) -> dict:
    p = Path(path) if path else default_cfg()
    if not p.is_absolute():
        p = state_root() / p
    raw = json.loads(p.read_text())
    cfg = {k: v for k, v in raw.items() if not str(k).startswith("_") and k != "comment"}
    cfg["_config_path"] = str(p)
    return cfg


def _to_dt(s: str | datetime | None) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.strptime(str(s), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def load_4h_bars(
    symbol: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    raw_dir: Path | None = None,
) -> list[dict]:
    path = (raw_dir or default_raw_dir()) / f"{symbol.upper()}_USD_5m.parquet"
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    if df.empty:
        return []
    df = df.copy()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    start_dt = _to_dt(start)
    end_dt = _to_dt(end)
    if end_dt is not None:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)
    if start_dt is not None:
        df = df[df["ts"] >= start_dt]
    if end_dt is not None:
        df = df[df["ts"] <= end_dt]
    if df.empty:
        return []
    df = df.set_index("ts").sort_index()
    out = pd.DataFrame(
        {
            "open": df["open"].resample("4h").first(),
            "high": df["high"].resample("4h").max(),
            "low": df["low"].resample("4h").min(),
            "close": df["close"].resample("4h").last(),
            "volume": df["volume"].resample("4h").sum(),
        }
    ).dropna()
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


def warmup_start(trade_start: datetime, cfg: dict) -> datetime:
    bar_minutes = int(cfg.get("bar_minutes", 240) or 240)
    slow = int(cfg.get("slow_ema_period", 200) or 200)
    daily = int(cfg.get("daily_ema_period", 0) or 0)
    need_hours = (slow + 10) * bar_minutes / 60.0
    need_days = need_hours / 24.0 + 2.0
    if daily > 0:
        need_days = max(need_days, float(daily + 15))
    return trade_start - timedelta(days=need_days)


def _max_dd(curve: list[tuple[int, float]]) -> float:
    if not curve:
        return 0.0
    peak = curve[0][1]
    worst = 0.0
    for _, eq in curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            worst = max(worst, (peak - eq) / peak)
    return float(worst)


def _regime_slices(curve: list[tuple[int, float, bool]]) -> dict:
    """Split equity path into bear vs bull occupancy."""
    if not curve:
        return {"bear_bars": 0, "bull_bars": 0, "bear_return_pct": 0.0, "bull_return_pct": 0.0}
    bear_bars = sum(1 for *_, bear in curve if bear)
    bull_bars = len(curve) - bear_bars
    first_eq = curve[0][1]
    last_eq = curve[-1][1]

    # product of bar returns inside each regime
    def prod(want_bear: bool) -> float:
        acc = 1.0
        prev = None
        for _, eq, bear in curve:
            if prev is not None and bear == want_bear and prev > 0:
                acc *= eq / prev
            prev = eq
        return acc - 1.0

    return {
        "bear_bars": bear_bars,
        "bull_bars": bull_bars,
        "bear_frac": bear_bars / max(len(curve), 1),
        "bear_return_pct": prod(True) * 100.0,
        "bull_return_pct": prod(False) * 100.0,
        "full_return_pct": ((last_eq / first_eq) - 1.0) * 100.0 if first_eq else 0.0,
    }


@dataclass
class ShortReplayResult:
    strategy_id: str
    symbols: list[str]
    start_usd: float
    end_equity: float
    end_cash: float
    trades: int
    wins: int
    losses: int
    max_dd_pct: float
    net_return_pct: float
    liquidations: int
    fees_paid: float
    funding_paid: float
    open_positions: int
    open_max: int
    bars: int
    first_trade_ts: int | None
    last_trade_ts: int | None
    trade_start: str | None
    trade_end: str | None
    warmup_from: str | None
    fee_rate: float
    funding_per_8h: float
    max_leverage: float
    integer_contracts: bool
    engine: str = "bear_regime_cover_margin1x"
    account_type: str = "MARGIN_1X"
    config: dict = field(default_factory=dict)
    config_path: str | None = None
    closed: list[dict] = field(default_factory=list)
    positions: dict = field(default_factory=dict)
    regime: dict = field(default_factory=dict)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "engine": self.engine,
            "account_type": self.account_type,
            "symbols": self.symbols,
            "start_usd": self.start_usd,
            "end_equity": round(self.end_equity, 4),
            "end_cash": round(self.end_cash, 4),
            "net_return_pct": round(self.net_return_pct * 100.0, 4),
            "max_dd_pct": round(self.max_dd_pct * 100.0, 4),
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "liquidations": self.liquidations,
            "fees_paid": round(self.fees_paid, 4),
            "funding_paid": round(self.funding_paid, 4),
            "open_positions": self.open_positions,
            "open_max": self.open_max,
            "bars": self.bars,
            "first_trade_ts": self.first_trade_ts,
            "last_trade_ts": self.last_trade_ts,
            "trade_start": self.trade_start,
            "trade_end": self.trade_end,
            "warmup_from": self.warmup_from,
            "fee_rate": self.fee_rate,
            "funding_per_8h": self.funding_per_8h,
            "max_leverage": self.max_leverage,
            "integer_contracts": self.integer_contracts,
            "config_path": self.config_path,
            "config": {k: v for k, v in self.config.items() if not str(k).startswith("_")},
            "regime": self.regime,
            "open_lots": self.positions,
        }


def run_short_replay(
    symbols: Iterable[str] | None = None,
    *,
    cfg: dict | None = None,
    config_path: str | Path | None = None,
    start: str | None = None,
    end: str | None = None,
    start_usd: float | None = None,
    fee_rate: float | None = None,
    funding_per_8h: float | None = None,
    integer_contracts: bool | None = None,
    raw_dir: Path | None = None,
) -> ShortReplayResult:
    raw_cfg = cfg if cfg is not None else load_cfg(config_path)
    syms = [s.upper() for s in (symbols if symbols is not None else raw_cfg.get("universe") or ["BTC", "ETH"])]
    start_usd_f = float(start_usd if start_usd is not None else raw_cfg.get("start_usd", 10000.0))
    fee = float(fee_rate if fee_rate is not None else raw_cfg.get("fee_rate", 0.0004))
    fund = float(funding_per_8h if funding_per_8h is not None else raw_cfg.get("funding_per_8h", 0.00003))
    whole = bool(raw_cfg.get("integer_contracts", False) if integer_contracts is None else integer_contracts)
    lev = float(raw_cfg.get("max_leverage", 1.0))

    trade_start_dt = _to_dt(start)
    trade_end_dt = _to_dt(end)
    data_start = warmup_start(trade_start_dt, raw_cfg) if trade_start_dt else None
    trade_start_ns = int(trade_start_dt.timestamp() * 1e9) if trade_start_dt else None

    raw = Path(raw_dir) if raw_dir else default_raw_dir()
    per: dict[str, list[dict]] = {}
    for s in syms:
        bars = load_4h_bars(s, start=data_start, end=trade_end_dt, raw_dir=raw)
        if bars:
            per[s] = bars

    engine = BearRegimeEngine(BearConfig.from_dict(raw_cfg), list(per.keys()))
    ledger = ShortLedger(
        start_usd=start_usd_f,
        max_leverage=lev,
        fee_rate=fee,
        funding_per_8h=fund,
        maintenance_margin_pct=float(raw_cfg.get("maintenance_margin_pct", 0.50)),
        integer_contracts=whole,
    )

    stream: list[tuple[int, int, str, dict]] = []
    for s, bars in per.items():
        prio = 0 if s == str(raw_cfg.get("regime_symbol", "BTC")).upper() else 1
        for b in bars:
            stream.append((int(b["ts"]), prio, s, b))
    stream.sort(key=lambda x: (x[0], x[1], x[2]))

    curve: list[tuple[int, float]] = []
    regime_curve: list[tuple[int, float, bool]] = []
    open_max = 0
    n_bars = 0
    first_trade = last_trade = None
    last_ts_seen = 0

    for ts, _prio, sym, bar in stream:
        emit = True if trade_start_ns is None else ts >= trade_start_ns
        px = float(bar["close"])
        ledger.update_price(sym, px, ts)
        if emit:
            ledger.apply_funding(ts)
            ledger.maybe_liquidate(ts, px)
        actions = engine.on_bar(sym, bar, emit=emit, positions=ledger.positions() if emit else None)
        if emit and actions:
            for a in actions:
                kind = str(a.get("type") or "").upper()
                apx = float(a.get("price", px))
                if kind == "COVER":
                    rec = ledger.cover(a["symbol"], apx, ts, str(a.get("reason") or "cover"))
                    if rec:
                        first_trade = first_trade or ts
                        last_trade = ts
                elif kind == "SHORT":
                    eq = ledger.equity()
                    alloc = float(a.get("alloc_pct") or engine.alloc_pct(a["symbol"]))
                    rec = ledger.short(
                        a["symbol"],
                        apx,
                        ts,
                        target_notional=eq * alloc,
                        reason=str(a.get("reason") or "regime_short"),
                    )
                    if rec:
                        first_trade = first_trade or ts
                        last_trade = ts
            ledger.maybe_liquidate(ts, px)
        n_bars += 1
        last_ts_seen = ts
        if emit:
            eq = ledger.equity()
            # one equity point per timestamp (after last symbol at that ts)
            if curve and curve[-1][0] == ts:
                curve[-1] = (ts, eq)
                regime_curve[-1] = (ts, eq, bool(engine.regime_bear))
            else:
                curve.append((ts, eq))
                regime_curve.append((ts, eq, bool(engine.regime_bear)))
            open_max = max(open_max, len(ledger.lots))

    end_eq = ledger.equity()
    return ShortReplayResult(
        strategy_id=str(raw_cfg.get("strategy_id", "bear_regime_cover")),
        symbols=list(per.keys()),
        start_usd=start_usd_f,
        end_equity=end_eq,
        end_cash=ledger.cash,
        trades=ledger.trades,
        wins=ledger.wins,
        losses=ledger.losses,
        max_dd_pct=_max_dd(curve),
        net_return_pct=(end_eq / start_usd_f - 1.0) if start_usd_f else 0.0,
        liquidations=ledger.liquidations,
        fees_paid=ledger.fees_paid,
        funding_paid=ledger.funding_paid,
        open_positions=len(ledger.lots),
        open_max=open_max,
        bars=n_bars,
        first_trade_ts=first_trade,
        last_trade_ts=last_trade,
        trade_start=start,
        trade_end=end,
        warmup_from=data_start.date().isoformat() if data_start else None,
        fee_rate=fee,
        funding_per_8h=fund,
        max_leverage=lev,
        integer_contracts=whole,
        config=raw_cfg,
        config_path=raw_cfg.get("_config_path"),
        closed=list(ledger.closed),
        positions=ledger.positions(),
        regime=_regime_slices(regime_curve),
        equity_curve=curve,
    )


def btc_buyhold(start_usd: float, start: str, end: str, raw_dir: Path | None = None) -> tuple[float, float]:
    bars = load_4h_bars("BTC", start=start, end=end, raw_dir=raw_dir)
    if len(bars) < 2:
        return start_usd, 0.0
    ret = bars[-1]["close"] / bars[0]["close"] - 1.0
    return start_usd * (1.0 + ret), ret * 100.0


def btc_short_hold(
    start_usd: float,
    start: str,
    end: str,
    *,
    fee_rate: float,
    funding_per_8h: float,
    raw_dir: Path | None = None,
) -> dict:
    """Always-short BTC 1x from first trade bar to last. Benchmark, not a candidate."""
    bars = load_4h_bars("BTC", start=start, end=end, raw_dir=raw_dir)
    if len(bars) < 2:
        return {"end_equity": start_usd, "return_pct": 0.0, "funding_paid": 0.0, "fees_paid": 0.0}
    led = ShortLedger(start_usd=start_usd, fee_rate=fee_rate, funding_per_8h=funding_per_8h, max_leverage=1.0)
    first, last = bars[0], bars[-1]
    led.update_price("BTC", first["close"], first["ts"])
    led.short("BTC", first["close"], first["ts"], target_notional=start_usd, reason="bench_open")
    for b in bars[1:]:
        led.update_price("BTC", b["close"], b["ts"])
        led.apply_funding(b["ts"])
    led.cover("BTC", last["close"], last["ts"], reason="bench_close")
    eq = led.equity()
    return {
        "end_equity": eq,
        "return_pct": (eq / start_usd - 1.0) * 100.0,
        "funding_paid": led.funding_paid,
        "fees_paid": led.fees_paid,
    }
