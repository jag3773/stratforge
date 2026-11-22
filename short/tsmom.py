"""
tsmom12_weekly — isolated weekly 12-month time-series momentum short.

Decision clock is the completed weekly close, not 4h. No ATR. Does not
import strategies.signal_engine or short.engine (4h EMA book).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _week_id(ts_ns: int, anchor: str = "W-SUN") -> str:
    """ISO week label. anchor kept for config compatibility; weeks are UTC Mon-Sun via isocalendar."""
    del anchor
    ts_s = int(ts_ns) / 1e9 if int(ts_ns) > 10**12 else int(ts_ns)
    d = datetime.fromtimestamp(ts_s, tz=timezone.utc).date()
    iso = d.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


@dataclass
class TsmomConfig:
    regime_symbol: str = "BTC"
    lookback_weeks: int = 52
    week_anchor: str = "W-SUN"
    btc_alloc_pct: float = 0.50
    eth_alloc_pct: float = 0.50
    eth_requires_own_bear: bool = True
    cat_stop_pct: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "TsmomConfig":
        return cls(
            regime_symbol=str(d.get("regime_symbol", "BTC")).upper(),
            lookback_weeks=int(d.get("lookback_weeks", 52) or 52),
            week_anchor=str(d.get("week_anchor", "W-SUN")),
            btc_alloc_pct=float(d.get("btc_alloc_pct", 0.50)),
            eth_alloc_pct=float(d.get("eth_alloc_pct", 0.50)),
            eth_requires_own_bear=bool(d.get("eth_requires_own_bear", True)),
            cat_stop_pct=float(d.get("cat_stop_pct", 0.0) or 0.0),
        )


@dataclass
class _Sym:
    week: str | None = None
    week_close: float | None = None
    weekly: list[float] = field(default_factory=list)
    ret_12m: float | None = None
    ready: bool = False
    bear: bool = False


class Tsmom12Engine:
    def __init__(self, cfg: TsmomConfig, symbols: list[str]):
        self.cfg = cfg
        self.symbols = [s.upper() for s in symbols]
        self.states: dict[str, _Sym] = {s: _Sym() for s in self.symbols}
        self.regime_bear = False
        self.regime_ready = False

    def alloc_pct(self, symbol: str) -> float:
        if symbol.upper() == "ETH":
            return float(self.cfg.eth_alloc_pct)
        return float(self.cfg.btc_alloc_pct)

    def _roll_week(self, st: _Sym, week: str, close: float) -> bool:
        """Close the prior week (if any). Returns True if a week was finalized."""
        if st.week is None:
            st.week = week
            st.week_close = float(close)
            return False
        if week == st.week:
            st.week_close = float(close)
            return False
        if st.week_close is None:
            st.week = week
            st.week_close = float(close)
            return False
        st.weekly.append(float(st.week_close))
        lb = int(self.cfg.lookback_weeks)
        if len(st.weekly) > lb:
            prev = st.weekly[-(lb + 1)]
            if prev > 0:
                st.ret_12m = st.weekly[-1] / prev - 1.0
                st.ready = True
                st.bear = bool(st.ret_12m < 0.0)
        st.week = week
        st.week_close = float(close)
        return True

    def on_bar(
        self,
        symbol: str,
        bar: dict,
        *,
        emit: bool = True,
        positions: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        sym = symbol.upper()
        st = self.states.get(sym)
        if st is None:
            return []
        c = float(bar["close"])
        ts = int(bar.get("ts") or 0)
        week = _week_id(ts, self.cfg.week_anchor)
        finalized = self._roll_week(st, week, c)

        if sym == self.cfg.regime_symbol:
            self.regime_ready = bool(st.ready)
            self.regime_bear = bool(st.bear)

        if not emit:
            return []

        actions: list[dict[str, Any]] = []
        pos = (positions or {}).get(sym)
        pos_side = (pos or {}).get("side")

        cat = float(self.cfg.cat_stop_pct)
        if pos_side == "SHORT" and cat > 0:
            entry = float((pos or {}).get("entry") or 0.0)
            if entry > 0 and c >= entry * (1.0 + cat):
                actions.append({"type": "COVER", "symbol": sym, "price": c, "reason": "cat_stop"})
                return actions

        if not finalized or not self.regime_ready:
            return actions

        want_short = bool(self.regime_bear)
        if sym != self.cfg.regime_symbol:
            if self.cfg.eth_requires_own_bear:
                want_short = bool(self.regime_bear and st.ready and st.bear)
            else:
                want_short = bool(self.regime_bear)

        if pos_side == "SHORT" and not want_short:
            actions.append({"type": "COVER", "symbol": sym, "price": c, "reason": "tsmom_flip"})
            return actions
        if pos_side == "SHORT":
            return actions
        if not want_short:
            return actions
        if not st.ready:
            return actions

        actions.append(
            {
                "type": "SHORT",
                "symbol": sym,
                "price": c,
                "reason": "tsmom12",
                "alloc_pct": self.alloc_pct(sym),
                "ret_12m": st.ret_12m,
            }
        )
        return actions
