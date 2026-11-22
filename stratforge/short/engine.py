"""
bear_regime_cover — short-only decision engine.

Cash in bulls. Short BTC (and ETH if also below its own slow EMA) when BTC 4h
close is below the BTC 200 EMA. Cover on regime flip or 2x ATR stop.

Does not import strategies.signal_engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from short.indicators import ATR, EMA


@dataclass
class SymbolState:
    ema: EMA
    atr: ATR
    bars: int = 0
    bars_since_trade: int = 10**9


@dataclass
class BearConfig:
    regime_symbol: str = "BTC"
    slow_ema_period: int = 200
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    cooldown_bars: int = 2
    cover_on_regime_flip: bool = True
    eth_requires_own_bear: bool = True
    btc_alloc_pct: float = 0.50
    eth_alloc_pct: float = 0.50
    max_concurrent: int = 2
    bar_minutes: int = 240
    # Optional slower-regime gates. All default off = baseline DNA.
    require_ema_falling: bool = False
    entry_persist_bars: int = 0
    daily_ema_period: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "BearConfig":
        return cls(
            regime_symbol=str(d.get("regime_symbol", "BTC")).upper(),
            slow_ema_period=int(d.get("slow_ema_period", 200)),
            atr_period=int(d.get("atr_period", 14)),
            atr_stop_mult=float(d.get("atr_stop_mult", 2.0)),
            cooldown_bars=int(d.get("cooldown_bars", 2)),
            cover_on_regime_flip=bool(d.get("cover_on_regime_flip", True)),
            eth_requires_own_bear=bool(d.get("eth_requires_own_bear", True)),
            btc_alloc_pct=float(d.get("btc_alloc_pct", 0.50)),
            eth_alloc_pct=float(d.get("eth_alloc_pct", 0.50)),
            max_concurrent=int(d.get("max_concurrent", 2)),
            bar_minutes=int(d.get("bar_minutes", 240)),
            require_ema_falling=bool(d.get("require_ema_falling", False)),
            entry_persist_bars=int(d.get("entry_persist_bars", 0) or 0),
            daily_ema_period=int(d.get("daily_ema_period", 0) or 0),
        )


class BearRegimeEngine:
    def __init__(self, cfg: BearConfig, symbols: list[str]):
        self.cfg = cfg
        self.symbols = [s.upper() for s in symbols]
        self.states: dict[str, SymbolState] = {
            s: SymbolState(ema=EMA(cfg.slow_ema_period), atr=ATR(cfg.atr_period))
            for s in self.symbols
        }
        self.regime_bear = False
        self.regime_ready = False
        self.ema_falling = False
        self._prev_regime_ema: float | None = None
        self.bear_persist = 0
        self.daily_ema = EMA(cfg.daily_ema_period) if cfg.daily_ema_period > 0 else None
        self.daily_bear = False
        self.daily_ready = False
        self._daily_date = None
        self._daily_close: float | None = None

    def alloc_pct(self, symbol: str) -> float:
        if symbol.upper() == "ETH":
            return float(self.cfg.eth_alloc_pct)
        return float(self.cfg.btc_alloc_pct)

    def name_bear(self, symbol: str, price: float) -> bool:
        st = self.states[symbol]
        if not st.ema.initialized:
            return False
        return float(price) < float(st.ema.value)

    def _update_daily(self, bar: dict, close: float) -> None:
        """Roll a daily 200-EMA from 4h bars. Entry-only gate; covers stay 4h."""
        if self.daily_ema is None:
            return
        ts = bar.get("ts")
        if ts is None:
            return
        ts_s = int(ts) / 1e9 if int(ts) > 10**12 else int(ts)
        day = datetime.fromtimestamp(ts_s, tz=timezone.utc).date()
        if self._daily_date is None:
            self._daily_date = day
            self._daily_close = float(close)
            return
        if day == self._daily_date:
            self._daily_close = float(close)
            return
        prev = float(self._daily_close if self._daily_close is not None else close)
        self.daily_ema.handle_bar(prev)
        if self.daily_ema.initialized:
            self.daily_ready = True
            self.daily_bear = prev < float(self.daily_ema.value)
        self._daily_date = day
        self._daily_close = float(close)

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
        o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        st.ema.handle_bar(c)
        st.atr.handle_bar(h, l, c)
        st.bars += 1
        st.bars_since_trade += 1

        if sym == self.cfg.regime_symbol and st.ema.initialized:
            self.regime_bear = c < st.ema.value
            self.regime_ready = True
            if self._prev_regime_ema is not None:
                self.ema_falling = st.ema.value < self._prev_regime_ema
            self._prev_regime_ema = float(st.ema.value)
            if self.regime_bear:
                self.bear_persist += 1
            else:
                self.bear_persist = 0
            self._update_daily(bar, c)

        if not emit:
            return []
        if not st.ema.initialized or not st.atr.initialized:
            return []
        if not self.regime_ready:
            return []

        pos = (positions or {}).get(sym)
        pos_side = (pos or {}).get("side")
        actions: list[dict[str, Any]] = []

        # ---- exits ----
        if pos_side == "SHORT" and st.bars_since_trade >= self.cfg.cooldown_bars:
            entry = float((pos or {}).get("entry") or 0.0)
            stop = entry + float(st.atr.value) * float(self.cfg.atr_stop_mult)
            if c >= stop and self.cfg.atr_stop_mult > 0:
                actions.append(
                    {"type": "COVER", "symbol": sym, "price": c, "reason": "atr_stop"}
                )
                st.bars_since_trade = 0
                if sym == self.cfg.regime_symbol:
                    self.bear_persist = 0
                return actions
            if self.cfg.cover_on_regime_flip and not self.regime_bear:
                actions.append(
                    {"type": "COVER", "symbol": sym, "price": c, "reason": "regime_flip"}
                )
                st.bars_since_trade = 0
                return actions
            if (
                sym != self.cfg.regime_symbol
                and self.cfg.eth_requires_own_bear
                and not self.name_bear(sym, c)
            ):
                actions.append(
                    {"type": "COVER", "symbol": sym, "price": c, "reason": "name_ema"}
                )
                st.bars_since_trade = 0
                return actions

        if pos_side == "SHORT":
            return actions
        if st.bars_since_trade < self.cfg.cooldown_bars:
            return actions
        if not self.regime_bear:
            return actions
        if self.cfg.require_ema_falling and not self.ema_falling:
            return actions
        if self.cfg.entry_persist_bars > 1 and self.bear_persist < self.cfg.entry_persist_bars:
            return actions
        if self.daily_ema is not None and (not self.daily_ready or not self.daily_bear):
            return actions

        # ---- entries (short only) ----
        open_n = sum(
            1
            for p in (positions or {}).values()
            if p and p.get("side") == "SHORT"
        )
        if open_n >= self.cfg.max_concurrent:
            return actions

        eligible = True
        if sym != self.cfg.regime_symbol and self.cfg.eth_requires_own_bear:
            eligible = self.name_bear(sym, c)
        if not eligible:
            return actions

        actions.append(
            {
                "type": "SHORT",
                "symbol": sym,
                "price": c,
                "reason": "regime_short",
                "alloc_pct": self.alloc_pct(sym),
            }
        )
        st.bars_since_trade = 0
        return actions
