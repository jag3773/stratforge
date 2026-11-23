"""
Framework-agnostic SignalEngine — the SINGLE source of truth for the trend-rotation
strategy's decisions. Pure Python (no Nautilus dependency) so it runs identically in:

  * research/backtests via strategies/research_replay.py (SignalEngine + CASH PaperExecutor)
  * the live paper node (native/REST feed -> this engine -> PaperExecutor)

This guarantees live paper decisions match the validated research path exactly.

Logic (from the original validated trend-rotation design):
  - 4h breakout entry (price > max of last channel_period highs)
  - trend filter (price > slow EMA) for longs
  - ride-the-trend exit (close < slow EMA), optional hard ATR stop while underwater
  - per-symbol concurrency + allocation caps + optional rotate_when_full

A bar is a plain dict: {"ts": int_ns_or_ms, "open","high","low","close","volume": float}.
The engine stores one indicator-set per symbol and returns a list of actions per bar:
  action = {"type": "BUY"|"SELL"|"CLOSE", "symbol": str, "reason": str}
The caller (PaperExecutor) translates BUY/SELL into position opens and CLOSE into exits.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


def breakout_pct(close: float, channel_high: float) -> float | None:
    """Raw extension through the prior channel high. None if undefined."""
    try:
        ch = float(channel_high)
        px = float(close)
    except (TypeError, ValueError):
        return None
    if ch <= 0:
        return None
    return px / ch - 1.0


def breakout_atr(close: float, channel_high: float, atr: float) -> float | None:
    """Extension through channel high in ATR units. None if undefined."""
    try:
        ch = float(channel_high)
        px = float(close)
        a = float(atr)
    except (TypeError, ValueError):
        return None
    if a <= 1e-12:
        return None
    return (px - ch) / a


def rank_entry_actions(actions: list[dict]) -> list[dict]:
    """CLOSE/SELL first, then BUY by score desc (None last), stable by symbol."""
    closes: list[dict] = []
    buys: list[dict] = []
    other: list[dict] = []
    for a in actions:
        t = str(a.get("type") or "")
        if t in {"CLOSE", "SELL"}:
            closes.append(a)
        elif t == "BUY":
            buys.append(a)
        else:
            other.append(a)

    def _buy_key(a: dict):
        sc = a.get("score")
        try:
            val = float(sc)
        except (TypeError, ValueError):
            val = float("-inf")
        return (-val, str(a.get("symbol") or ""))

    buys.sort(key=_buy_key)
    return closes + other + buys


class RSI:
    """Wilder RSI. .value is 0..100 (matches spec zones, unlike Nautilus built-in)."""
    def __init__(self, period: int = 14):
        self.period = period
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._prev = None
        self._n = 0
        self.value = 50.0
        self.initialized = False

    def handle_bar(self, close: float):
        if self._prev is None:
            self._prev = close
            return
        ch = close - self._prev
        gain = ch if ch > 0 else 0.0
        loss = -ch if ch < 0 else 0.0
        self._prev = close
        if not self.initialized:
            self._avg_gain += gain
            self._avg_loss += loss
            self._n += 1
            if self._n >= self.period:
                self._avg_gain /= self.period
                self._avg_loss /= self.period
                self.initialized = True
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        if self._avg_loss < 1e-12:
            self.value = 100.0 if self._avg_gain > 1e-12 else 50.0
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = 100.0 - (100.0 / (1.0 + rs))


class EMA:
    def __init__(self, period: int):
        self.period = period
        self._alpha = 2.0 / (period + 1.0)
        self.value = 0.0
        self.initialized = False
        self._n = 0

    def handle_bar(self, price: float):
        if not self.initialized:
            self.value = price
            self.initialized = True
        else:
            self.value = self._alpha * price + (1.0 - self._alpha) * self.value


class ATR:
    def __init__(self, period: int = 14):
        self.period = period
        self._prev_close = None
        self._atr = 0.0
        self._n = 0
        self.value = 0.0
        self.initialized = False

    def handle_bar(self, high: float, low: float, close: float):
        if self._prev_close is None:
            tr = high - low
            self._prev_close = close
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            self._prev_close = close
        if not self.initialized:
            self._atr += tr
            self._n += 1
            if self._n >= self.period:
                self._atr /= self.period
                self.value = self._atr
                self.initialized = True
        else:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period
            self.value = self._atr


class ADX:
    """Wilder-smoothed ADX (Nautilus built-in ADX/.value is broken in v1.221.0)."""
    def __init__(self, period: int = 14):
        self.period = period
        self._atr = ATR(period)
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None
        self._tr = self._pdm = self._mdm = self._adx = self._adx_sum = 0.0
        self._n = self._m = 0
        self._seeded = self._init_adx = False
        self.value = 0.0
        self.initialized = False

    def handle_bar(self, high: float, low: float, close: float):
        self._atr.handle_bar(high, low, close)
        if self._prev_close is None:
            self._prev_high, self._prev_low, self._prev_close = high, low, close
            return
        tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        up = high - self._prev_high
        dn = self._prev_low - low
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0
        self._prev_high, self._prev_low, self._prev_close = high, low, close
        k = 1.0 / self.period
        if not self._seeded:
            self._tr += tr; self._pdm += pdm; self._mdm += mdm; self._n += 1
            if self._n >= self.period:
                self._tr /= self.period; self._pdm /= self.period; self._mdm /= self.period
                self._seeded = True
            return
        self._tr = (self._tr * (self.period - 1) + tr) * k
        self._pdm = (self._pdm * (self.period - 1) + pdm) * k
        self._mdm = (self._mdm * (self.period - 1) + mdm) * k
        trv = self._tr if self._tr > 1e-12 else 1e-12
        pdi = 100.0 * self._pdm / trv
        mdi = 100.0 * self._mdm / trv
        s = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / s if s > 1e-12 else 0.0
        if not self._init_adx:
            self._adx_sum += dx; self._m += 1
            if self._m >= self.period:
                self._adx = self._adx_sum / self.period
                self._init_adx = True
                self.initialized = True
        else:
            self._adx = (self._adx * (self.period - 1) + dx) * k
        self.value = self._adx


@dataclass
class SymbolState:
    rsi: RSI
    fast_ema: EMA
    slow_ema: EMA
    adx: ADX
    atr: ATR
    hh: list = field(default_factory=list)
    ll: list = field(default_factory=list)
    bars_since_trade: int = 999
    trail_ext: Optional[float] = None
    initialized: bool = False


@dataclass
class SignalConfig:
    mode: str = "breakout"
    channel_period: int = 60
    fast_ema_period: int = 21
    slow_ema_period: int = 200
    adx_period: int = 14
    adx_threshold: float = 20.0
    atr_period: int = 14
    atr_stop_mult: float = 0.0
    atr_tp_mult: float = 2.0
    cooldown_bars: int = 2
    use_adx_filter: bool = False
    trend_filter: bool = True
    long_only: bool = True
    exit_mode: str = "slow_ema"
    slow_ema_buffer: float = 0.0
    # False: slow-EMA sells only while price is still above entry (underwater
    # names have no price exit when atr_stop_mult is 0). True: a close below
    # the slow EMA sells whether the lot is green or red. Not a hard stop.
    slow_ema_exit_underwater: bool = False
    max_concurrent: int = 50
    max_alloc_pct: float = 0.25
    rsi_period: int = 14
    # When at max_concurrent and a NEW long signal appears on a flat name: close the
    # weakest underwater long (MTM% < 0) held ≥ rotate_min_hold_bars, then enter.
    # False = skip new entry until a natural slow-EMA exit frees a slot.
    rotate_when_full: bool = False
    rotate_min_hold_bars: int = 14
    bar_minutes: int = 240

    @classmethod
    def from_dict(cls, cfg: dict) -> "SignalConfig":
        """Load knobs from strategy JSON. Missing keys use production-safe defaults
        (max_concurrent=8). Dataclass field defaults stay 50 for bare constructors."""
        return cls(
            mode=cfg.get("mode", "breakout"),
            channel_period=int(cfg.get("channel_period", 60)),
            fast_ema_period=int(cfg.get("fast_ema_period", 21)),
            slow_ema_period=int(cfg.get("slow_ema_period", 200)),
            adx_period=int(cfg.get("adx_period", 14)),
            adx_threshold=float(cfg.get("adx_threshold", 20.0)),
            atr_period=int(cfg.get("atr_period", 14)),
            atr_stop_mult=float(cfg.get("atr_stop_mult", 0.0)),
            atr_tp_mult=float(cfg.get("atr_tp_mult", 2.0)),
            cooldown_bars=int(cfg.get("cooldown_bars", 2)),
            use_adx_filter=bool(cfg.get("use_adx_filter", False)),
            trend_filter=bool(cfg.get("trend_filter", True)),
            long_only=bool(cfg.get("long_only", True)),
            exit_mode=cfg.get("exit_mode", "slow_ema"),
            slow_ema_buffer=float(cfg.get("slow_ema_buffer", 0.0)),
            slow_ema_exit_underwater=bool(cfg.get("slow_ema_exit_underwater", False)),
            max_concurrent=int(cfg.get("max_concurrent", 8)),
            max_alloc_pct=float(cfg.get("max_alloc_pct", 0.25)),
            rsi_period=int(cfg.get("rsi_period", 14)),
            rotate_when_full=bool(cfg.get("rotate_when_full", False)),
            rotate_min_hold_bars=int(cfg.get("rotate_min_hold_bars", 14) or 14),
            bar_minutes=int(cfg.get("bar_minutes", 240) or 240),
        )


class SignalEngine:
    """
    One decision engine for the whole universe. Call on_bar(symbol, bar) for each
    incoming 4h bar. Returns a list of action dicts for symbols that changed state.
    Position bookkeeping (open/flat per symbol, concurrency count) is supplied by the
    caller via the `positions` dict: {symbol: {"side": "LONG"|"SHORT"|None,
    "entry": float, "ts": optional_ns}}. This keeps the engine free of execution/account concerns.

    Slot ledger (`_long_slots`) is authoritative under same-bar multi-symbol fills so
    max_concurrent cannot be exceeded when many coins share one 4h ts_event.
    """

    def __init__(self, config: SignalConfig, symbols: list[str]):
        self.cfg = config
        self.symbols = symbols
        self.states: dict[str, SymbolState] = {}
        for s in symbols:
            self.states[s] = SymbolState(
                rsi=RSI(config.rsi_period),
                fast_ema=EMA(config.fast_ema_period),
                slow_ema=EMA(config.slow_ema_period),
                adx=ADX(config.adx_period),
                atr=ATR(config.atr_period),
            )
        self.history_count = 0
        self.last_px: dict[str, float] = {}
        # Authoritative open-long set (symbol strings). Synced from positions + local updates.
        self._long_slots: set[str] = set()

    def feed_history(self, symbol: str, bars: list[dict]):
        """Seed indicators with historical bars (no actions emitted)."""
        for b in bars:
            self.on_bar(symbol, b, emit=False)

    def sync_slots_from_positions(self, positions: Optional[dict] = None):
        """Rebuild slot ledger from executor book (call after journal restore)."""
        self._long_slots = {
            str(sym)
            for sym, p in (positions or {}).items()
            if (p or {}).get("side") == "LONG"
        }

    def note_fill(self, action_type: str, symbol: str):
        """Optional external slot update after executor.apply (BUY/CLOSE)."""
        if action_type == "BUY":
            self._long_slots.add(symbol)
        elif action_type in ("CLOSE", "SELL"):
            self._long_slots.discard(symbol)

    def _sync_slots_soft(self, positions: Optional[dict]):
        """Union executor longs into slots; drop slots no longer open."""
        open_longs = {
            str(sym)
            for sym, p in (positions or {}).items()
            if (p or {}).get("side") == "LONG"
        }
        # Drop closed; add any executor longs we missed (restart / external).
        self._long_slots &= open_longs
        self._long_slots |= open_longs

    def _min_hold_ns(self) -> int:
        bars = int(getattr(self.cfg, "rotate_min_hold_bars", 14) or 14)
        mins = int(getattr(self.cfg, "bar_minutes", 240) or 240)
        return int(max(mins, 1) * 60 * 1_000_000_000 * max(bars, 1))

    def _bar_ts_ns(self, bar: dict) -> int:
        ts = bar.get("ts")
        if ts is None:
            return 0
        try:
            v = int(ts)
        except (TypeError, ValueError):
            return 0
        if v > 10**17:
            return v
        if v > 10**14:
            return v * 1_000
        if v > 10**11:
            return v * 1_000_000
        return v * 1_000_000_000

    def _pick_rotate_victim(
        self, positions: Optional[dict], bar: dict, exclude: str
    ) -> Optional[tuple[str, float, float]]:
        """
        Weakest underwater long eligible for rotation.
        Returns (symbol, mtm_ret, mark_px) or None.
        Never selects winners (ret >= 0) or names held < rotate_min_hold_bars.
        """
        if not positions:
            return None
        bar_ts = self._bar_ts_ns(bar)
        min_hold = self._min_hold_ns()
        eligible: list[tuple[str, float, float]] = []
        for sym, p in positions.items():
            if not p or p.get("side") != "LONG":
                continue
            if sym == exclude:
                continue
            if sym not in self._long_slots:
                continue
            entry = float(p.get("entry") or 0.0)
            if entry <= 0:
                continue
            mark = float(self.last_px.get(sym) or entry)
            ret = (mark / entry) - 1.0
            if ret >= 0.0:
                continue  # never force-rotate winners
            ts_o = int(p.get("ts") or 0)
            if ts_o > 0 and bar_ts > 0 and (bar_ts - ts_o) < min_hold:
                continue
            # If timestamps missing, require bars_since_trade on that symbol >= min hold.
            if ts_o <= 0 or bar_ts <= 0:
                st_v = self.states.get(sym)
                min_bars = int(getattr(self.cfg, "rotate_min_hold_bars", 14) or 14)
                if st_v is None or st_v.bars_since_trade < min_bars:
                    continue
            eligible.append((sym, ret, mark))
        if not eligible:
            return None
        eligible.sort(key=lambda x: x[1])
        return eligible[0]

    def peek_breakout_pct(self, symbol: str) -> float | None:
        """Score after indicators already include this bar. Does not emit."""
        st = self.states.get(symbol)
        if st is None or len(st.hh) < 2:
            return None
        price = float(self.last_px.get(symbol) or 0.0)
        chan_h = max(st.hh[:-1])
        return breakout_pct(price, chan_h)

    def peek_breakout_atr(self, symbol: str) -> float | None:
        st = self.states.get(symbol)
        if st is None or len(st.hh) < 2:
            return None
        price = float(self.last_px.get(symbol) or 0.0)
        chan_h = max(st.hh[:-1])
        atr = float(getattr(st.atr, "value", 0.0) or 0.0)
        return breakout_atr(price, chan_h, atr)

    def peek_entry_score(self, symbol: str, rank: str) -> float | None:
        if rank == "breakout_atr":
            return self.peek_breakout_atr(symbol)
        if rank == "breakout_pct":
            return self.peek_breakout_pct(symbol)
        return None

    def decide(self, symbol: str, bar: dict, positions: Optional[dict] = None) -> list[dict]:
        """Emit actions from already-updated state (no second indicator tick)."""
        return self.on_bar(symbol, bar, emit=True, positions=positions, update=False)

    def on_bar(self, symbol: str, bar: dict, emit: bool = True,
               positions: Optional[dict] = None, update: bool = True) -> list[dict]:
        st = self.states.get(symbol)
        if st is None:
            return []
        o, h, l, c, v = bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
        if update:
            st.rsi.handle_bar(c)
            st.fast_ema.handle_bar(c)
            st.slow_ema.handle_bar(c)
            st.adx.handle_bar(h, l, c)
            st.atr.handle_bar(h, l, c)
            price = float(c)
            self.last_px[symbol] = price
            st.hh.append(price); st.ll.append(price)
            if len(st.hh) > self.cfg.channel_period:
                st.hh.pop(0); st.ll.pop(0)
        else:
            price = float(c)
            self.last_px[symbol] = price

        ready = (st.rsi.initialized and st.fast_ema.initialized and st.slow_ema.initialized
                 and st.adx.initialized and st.atr.initialized
                 and len(st.hh) >= self.cfg.channel_period)
        if not ready:
            return []

        # When seeding history, caller must pass positions=None and we skip signals.
        if not emit:
            return []

        # Do NOT rebuild slots from positions every bar: same-cycle multi-symbol
        # fills require the local ledger until the caller applies and re-syncs.
        # Startup: engine.sync_slots_from_positions(executor.positions).

        pos = (positions or {}).get(symbol)
        pos_side = (pos or {}).get("side") if pos else None
        is_long = pos_side == "LONG"
        is_short = pos_side == "SHORT"

        st.bars_since_trade += 1
        actions: list[dict] = []

        # ---- EXIT ----
        if pos_side is not None and st.bars_since_trade >= self.cfg.cooldown_bars:
            entry = float(pos["entry"])
            s = float(st.atr.value) * self.cfg.atr_stop_mult
            if st.trail_ext is None:
                st.trail_ext = price
            st.trail_ext = max(st.trail_ext, price) if is_long else min(st.trail_ext, price)
            ext = st.trail_ext

            if self.cfg.exit_mode == "slow_ema":
                if is_long:
                    if self.cfg.atr_stop_mult > 0 and price <= entry - s:
                        actions.append({"type": "CLOSE", "symbol": symbol, "reason": "hard_stop",
                                        "price": price})
                        st.trail_ext = None
                        self._long_slots.discard(symbol)
                        return actions
                    if self.cfg.slow_ema_exit_underwater or price > entry:
                        buf = st.slow_ema.value * (1.0 - self.cfg.slow_ema_buffer / 100.0)
                        if price < buf:
                            actions.append({"type": "CLOSE", "symbol": symbol,
                                            "reason": "slow_ema_exit", "price": price})
                            st.trail_ext = None
                            self._long_slots.discard(symbol)
                            return actions
                else:  # short (only if long_only disabled)
                    if self.cfg.atr_stop_mult > 0 and price >= entry + s:
                        actions.append({"type": "CLOSE", "symbol": symbol, "reason": "hard_stop",
                                        "price": price})
                        st.trail_ext = None
                        return actions
                    if self.cfg.slow_ema_exit_underwater or price < entry:
                        buf = st.slow_ema.value * (1.0 + self.cfg.slow_ema_buffer / 100.0)
                        if price > buf:
                            actions.append({"type": "CLOSE", "symbol": symbol,
                                            "reason": "slow_ema_exit", "price": price})
                            st.trail_ext = None
                            return actions
            else:
                # generic ATR stop/tp (non-slow_ema modes; not used by our config)
                if is_long and (price <= entry - s or price >= entry + s * self.cfg.atr_tp_mult):
                    actions.append({"type": "CLOSE", "symbol": symbol, "reason": "atr_exit",
                                    "price": price})
                    st.trail_ext = None
                    self._long_slots.discard(symbol)
                    return actions
                if is_short and (price >= entry + s or price <= entry - s * self.cfg.atr_tp_mult):
                    actions.append({"type": "CLOSE", "symbol": symbol, "reason": "atr_exit",
                                    "price": price})
                    st.trail_ext = None
                    return actions

        if st.bars_since_trade < self.cfg.cooldown_bars:
            return actions

        # ---- ENTRY signals ----
        long_sig = short_sig = False
        is_trending = st.adx.value >= self.cfg.adx_threshold
        chan_h = 0.0
        if self.cfg.mode == "breakout":
            chan_h = max(st.hh[:-1]); chan_l = min(st.ll[:-1])
            if price > chan_h:
                long_sig = True
            elif price < chan_l:
                short_sig = True
        elif self.cfg.mode == "trend":
            if st.fast_ema.value > st.slow_ema.value:
                long_sig = True
            elif st.fast_ema.value < st.slow_ema.value:
                short_sig = True
        # (reversion/dip/rsi2 modes omitted — not used by the winning config)

        if self.cfg.use_adx_filter and not is_trending:
            long_sig = short_sig = False
        if self.cfg.long_only:
            short_sig = False

        above_slow = price > st.slow_ema.value
        below_slow = price < st.slow_ema.value

        if long_sig:
            if self.cfg.trend_filter and below_slow:
                return actions
            # Already long this name — no add / no self-rotate.
            if pos_side == "LONG" or symbol in self._long_slots:
                return actions

            n_slots = len(self._long_slots)
            if n_slots >= self.cfg.max_concurrent:
                if not getattr(self.cfg, "rotate_when_full", False):
                    return actions  # admission control only
                victim = self._pick_rotate_victim(positions, bar, exclude=symbol)
                if victim is None:
                    return actions
                vsym, _vret, vpx = victim
                if vsym == symbol:
                    return actions
                actions.append({
                    "type": "CLOSE",
                    "symbol": vsym,
                    "price": float(vpx),
                    "reason": "rotate_out",
                })
                self._long_slots.discard(vsym)
                vst = self.states.get(vsym)
                if vst is not None:
                    vst.trail_ext = None

            # Final local cap check (after possible rotate).
            if len(self._long_slots) >= self.cfg.max_concurrent:
                return actions

            actions.append({"type": "BUY", "symbol": symbol, "price": price,
                            "reason": "breakout_long",
                            "score": breakout_pct(price, chan_h),
                            "score_name": "breakout_pct"})
            self._long_slots.add(symbol)
            st.bars_since_trade = 0
            st.trail_ext = price
        elif short_sig:
            if self.cfg.trend_filter and above_slow:
                return actions
            if pos_side != "SHORT":
                actions.append({"type": "SELL", "symbol": symbol, "price": price,
                                "reason": "breakout_short"})
                st.bars_since_trade = 0
                st.trail_ext = price
        return actions
