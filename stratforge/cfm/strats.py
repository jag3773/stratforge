"""Literature CFM identities. Not a port of trend_rotation_25_max8_rotate.

Signals computed on closed bars; replay fills at next open.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Intent:
    symbol: str
    side: str  # LONG SHORT FLAT
    leverage: float
    stop_px: float | None = None
    reason: str = ""
    hold_contracts: bool = False


def _ema(prev: float | None, price: float, n: int) -> float:
    if prev is None:
        return float(price)
    k = 2.0 / (n + 1.0)
    return k * price + (1.0 - k) * prev


def _atr_step(prev: float | None, high: float, low: float, prev_close: float, n: int) -> float:
    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
    if prev is None:
        return tr
    return (prev * (n - 1) + tr) / n


class Tsmom12:
    """Weekly 12-month time-series momentum, long/short, target leverage.

    Moskowitz/Ooi/Pedersen 2012. Lookback 365d on 4h closes. Decision on
    Monday 00:00 UTC week bucket. Hold until flip. No ATR.
    """

    def __init__(self, symbols: list[str], leverage: float, lookback_days: int = 365, short_enabled: bool = True):
        self.symbols = [s.upper() for s in symbols]
        self.leverage = float(leverage)
        self.lookback_bars = int(lookback_days * 6)  # 4h
        self.short_enabled = bool(short_enabled)
        self._closes: dict[str, list[float]] = {s: [] for s in self.symbols}
        self._week: dict[str, tuple[str, float]] = {}
        self._issued = False
        self.strategy_id = (
            f"cfm_tsmom{lookback_days}d_{'ls' if short_enabled else 'long'}_lev{leverage:g}_"
            + "_".join(self.symbols).lower()
        )

    def on_close(self, ts: int, closes: dict[str, float], highs: dict, lows: dict) -> list[Intent]:
        for s in self.symbols:
            if s in closes:
                self._closes[s].append(float(closes[s]))
        # weekly: 42 4h bars/week; decide when ts hour is Monday 00:00 — ns
        # 4h bars: decide every 42 bars after warmup, using last vs lookback
        n = min(len(v) for v in self._closes.values()) if self._closes else 0
        if n <= self.lookback_bars:
            return [Intent(s, "FLAT", 0.0, reason="warmup") for s in self.symbols]
        if n % 42 != 0 and self._issued:
            return []  # hold prior
        self._issued = True
        out: list[Intent] = []
        n_on = sum(1 for s in self.symbols if s in closes)
        if n_on <= 0:
            return out
        lev_each = self.leverage / n_on
        for s in self.symbols:
            if s not in closes:
                continue
            series = self._closes[s]
            ret = series[-1] / series[-1 - self.lookback_bars] - 1.0
            if ret > 0:
                out.append(Intent(s, "LONG", lev_each, reason="tsmom+"))
            elif self.short_enabled:
                out.append(Intent(s, "SHORT", lev_each, reason="tsmom-"))
            else:
                out.append(Intent(s, "FLAT", 0.0, reason="tsmom-flat"))
        return out


class DonchianTurtle:
    """Classic turtle: N-bar Donchian breakout, n-bar exit, ATR stop, 2% unit risk, cap leverage."""

    def __init__(
        self,
        symbols: list[str],
        entry_n: int = 330,
        exit_n: int = 120,
        atr_n: int = 120,
        risk_frac: float = 0.02,
        atr_stop_mult: float = 2.0,
        max_leverage: float = 4.0,
        short_enabled: bool = True,
    ):
        self.symbols = [s.upper() for s in symbols]
        # Defaults are 4h-bar counts. 55/20 *days* = 330/120 bars.
        self.entry_n = int(entry_n)
        self.exit_n = int(exit_n)
        self.atr_n = int(atr_n)
        self.risk_frac = float(risk_frac)
        self.atr_stop_mult = float(atr_stop_mult)
        self.max_leverage = float(max_leverage)
        self.short_enabled = bool(short_enabled)
        self._h: dict[str, list[float]] = {s: [] for s in self.symbols}
        self._l: dict[str, list[float]] = {s: [] for s in self.symbols}
        self._c: dict[str, list[float]] = {s: [] for s in self.symbols}
        self._atr: dict[str, float | None] = {s: None for s in self.symbols}
        self._side: dict[str, str] = {s: "FLAT" for s in self.symbols}
        self._last_sent: dict[str, str] = {s: "" for s in self.symbols}
        self.strategy_id = (
            f"cfm_turtle_{entry_n}_{exit_n}_r{risk_frac:g}_atr{atr_stop_mult:g}"
            f"_{'ls' if short_enabled else 'long'}_" + "_".join(self.symbols).lower()
        )

    def on_close(self, ts: int, closes: dict[str, float], highs: dict[str, float], lows: dict[str, float]) -> list[Intent]:
        out: list[Intent] = []
        for s in self.symbols:
            if s not in closes or s not in highs or s not in lows:
                continue
            h, l, c = float(highs[s]), float(lows[s]), float(closes[s])
            prev_c = self._c[s][-1] if self._c[s] else c
            self._atr[s] = _atr_step(self._atr[s], h, l, prev_c, self.atr_n)
            self._h[s].append(h)
            self._l[s].append(l)
            self._c[s].append(c)
            need = max(self.entry_n, self.exit_n) + 2
            if len(self._c[s]) < need:
                out.append(Intent(s, "FLAT", 0.0, reason="warmup"))
                continue
            hh = max(self._h[s][-self.entry_n - 1 : -1])
            ll = min(self._l[s][-self.entry_n - 1 : -1])
            xh = max(self._h[s][-self.exit_n - 1 : -1])
            xl = min(self._l[s][-self.exit_n - 1 : -1])
            atr = float(self._atr[s] or 0.0)
            side = self._side[s]
            if side == "FLAT":
                if c > hh:
                    stop = c - self.atr_stop_mult * atr if atr > 0 else None
                    self._side[s] = "LONG"
                    out.append(Intent(s, "LONG", self._lev(c, atr), stop, "break_up"))
                    self._last_sent[s] = "LONG"
                elif self.short_enabled and c < ll:
                    stop = c + self.atr_stop_mult * atr if atr > 0 else None
                    self._side[s] = "SHORT"
                    out.append(Intent(s, "SHORT", self._lev(c, atr), stop, "break_dn"))
                    self._last_sent[s] = "SHORT"
                elif self._last_sent[s] != "FLAT":
                    out.append(Intent(s, "FLAT", 0.0, reason="no_break"))
                    self._last_sent[s] = "FLAT"
            elif side == "LONG":
                if c < xl:
                    self._side[s] = "FLAT"
                    out.append(Intent(s, "FLAT", 0.0, reason="exit_ch"))
                    self._last_sent[s] = "FLAT"
                else:
                    if self._last_sent[s] != "LONG":
                        stop = c - self.atr_stop_mult * atr if atr > 0 else None
                        out.append(Intent(s, "LONG", self._lev(c, atr), stop, "hold_long"))
                        self._last_sent[s] = "LONG"
            elif side == "SHORT":
                if (not self.short_enabled) or c > xh:
                    self._side[s] = "FLAT"
                    out.append(Intent(s, "FLAT", 0.0, reason="exit_ch"))
                    self._last_sent[s] = "FLAT"
                else:
                    if self._last_sent[s] != "SHORT":
                        stop = c + self.atr_stop_mult * atr if atr > 0 else None
                        out.append(Intent(s, "SHORT", self._lev(c, atr), stop, "hold_short"))
                        self._last_sent[s] = "SHORT"
        return out

    def _lev(self, price: float, atr: float) -> float:
        if atr <= 0 or price <= 0:
            return min(1.0, self.max_leverage)
        # 2% equity risk at atr_stop_mult ATR → notional/equity = risk / (stop_frac)
        stop_frac = self.atr_stop_mult * atr / price
        if stop_frac <= 1e-9:
            return self.max_leverage
        lev = self.risk_frac / stop_frac
        return float(min(self.max_leverage, max(0.0, lev)))


class EmaRegimeLev:
    """Daily-ish 50/200 EMA regime on 4h (50d=300 bars, 200d=1200). Long lev when fast>slow, else short or flat."""

    def __init__(self, symbols: list[str], leverage: float, fast: int = 300, slow: int = 1200, short_enabled: bool = True):
        self.symbols = [s.upper() for s in symbols]
        self.leverage = float(leverage)
        self.fast_n = int(fast)
        self.slow_n = int(slow)
        self.short_enabled = bool(short_enabled)
        self._f: dict[str, float | None] = {s: None for s in self.symbols}
        self._s: dict[str, float | None] = {s: None for s in self.symbols}
        self._n: dict[str, int] = {s: 0 for s in self.symbols}
        self._issued = 0
        self.strategy_id = (
            f"cfm_ema{fast}_{slow}_{'ls' if short_enabled else 'long'}_lev{leverage:g}_"
            + "_".join(self.symbols).lower()
        )

    def on_close(self, ts: int, closes: dict[str, float], highs: dict, lows: dict) -> list[Intent]:
        out: list[Intent] = []
        live = [s for s in self.symbols if s in closes]
        if not live:
            return out
        self._issued += 1
        ready = True
        for s in live:
            c = float(closes[s])
            self._f[s] = _ema(self._f[s], c, self.fast_n)
            self._s[s] = _ema(self._s[s], c, self.slow_n)
            self._n[s] += 1
            if self._n[s] < self.slow_n:
                ready = False
        if not ready:
            return [Intent(s, "FLAT", 0.0, reason="warmup") for s in live]
        if self._issued % 42 != 1:
            return []
        lev_each = self.leverage / len(live)
        for s in live:
            fv, sv = self._f[s], self._s[s]
            if fv is None or sv is None:
                out.append(Intent(s, "FLAT", 0.0, reason="warmup"))
                continue
            if fv > sv:
                out.append(Intent(s, "LONG", lev_each, reason="ema_bull"))
            elif self.short_enabled:
                out.append(Intent(s, "SHORT", lev_each, reason="ema_bear"))
            else:
                out.append(Intent(s, "FLAT", 0.0, reason="ema_bear_flat"))
        return out


class VolTargetLong:
    """Always-long BTC (or symbols), leverage = target_ann_vol / realized_ann_vol, cap 4x.

    Realized vol = 30d std of 4h log-returns * sqrt(6*365).
    """

    def __init__(self, symbols: list[str], target_vol: float = 0.40, vol_lookback_days: int = 30, max_leverage: float = 4.0):
        self.symbols = [s.upper() for s in symbols]
        self.target_vol = float(target_vol)
        self.lb = int(vol_lookback_days * 6)
        self.max_leverage = float(max_leverage)
        self._c: dict[str, list[float]] = {s: [] for s in self.symbols}
        self._issued = 0
        self.strategy_id = (
            f"cfm_voltarget_{target_vol:g}_{vol_lookback_days}d_long_" + "_".join(self.symbols).lower()
        )

    def on_close(self, ts: int, closes: dict[str, float], highs: dict, lows: dict) -> list[Intent]:
        import math

        out: list[Intent] = []
        live = [s for s in self.symbols if s in closes]
        if not live:
            return out
        for s in live:
            self._c[s].append(float(closes[s]))
        self._issued += 1
        if any(len(self._c[s]) < self.lb + 2 for s in live):
            return [Intent(s, "FLAT", 0.0, reason="warmup") for s in live]
        if self._issued % 42 != 1:
            return []
        for s in live:
            xs = self._c[s]
            rets = [math.log(xs[i] / xs[i - 1]) for i in range(-self.lb, 0) if xs[i - 1] > 0]
            if len(rets) < 10:
                out.append(Intent(s, "FLAT", 0.0, reason="warmup"))
                continue
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
            std = math.sqrt(max(var, 0.0))
            ann = std * math.sqrt(6 * 365)
            lev = 0.0 if ann <= 1e-9 else min(self.max_leverage, self.target_vol / ann)
            lev = lev / len(live)
            out.append(Intent(s, "LONG", lev, reason=f"vt_{ann:.2f}"))
        return out


class Hold1x:
    def __init__(self, symbols: list[str] | None = None):
        self.symbols = [s.upper() for s in (symbols or ["BTC"])]
        self.strategy_id = "cfm_btc_1x_hold" if self.symbols == ["BTC"] else "cfm_hold_1x_" + "_".join(x.lower() for x in self.symbols)
        self._done = False

    def on_close(self, ts: int, closes: dict[str, float], highs: dict, lows: dict) -> list[Intent]:
        if self._done:
            return []
        self._done = True
        n = len([s for s in self.symbols if s in closes]) or 1
        return [Intent(s, "LONG", 1.0 / n, reason="hold") for s in self.symbols if s in closes]


class WeeklySma:
    """Weekly close vs N-week SMA. Faber/CTA filter. Decision on weekly bars only."""

    def __init__(
        self,
        symbols: list[str],
        n: int = 10,
        leverage: float = 2.0,
        short_enabled: bool = False,
        hold_contracts: bool = True,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.n = int(n)
        self.leverage = float(leverage)
        self.short_enabled = bool(short_enabled)
        self.hold_contracts = bool(hold_contracts)
        self._week: int | None = None
        self._week_close: dict[str, float] = {}
        self._w: dict[str, list[float]] = {s: [] for s in self.symbols}
        self.flips = 0
        self.weeks = 0
        self._last_side: dict[str, str] = {s: "FLAT" for s in self.symbols}
        mode = "ls" if short_enabled else "long"
        hc = "holdc" if hold_contracts else "rebal"
        self.strategy_id = f"cfm_wsma{n}_{mode}_lev{leverage:g}_{hc}_" + "_".join(x.lower() for x in self.symbols)

    @staticmethod
    def _iso_week(ts: int) -> int:
        from datetime import datetime, timezone

        ic = datetime.fromtimestamp(ts / 1_000_000_000, tz=timezone.utc).isocalendar()
        return int(ic.year) * 100 + int(ic.week)

    def on_close(self, ts: int, closes: dict[str, float], highs: dict, lows: dict) -> list[Intent]:
        live = [s for s in self.symbols if s in closes]
        if not live:
            return []
        wid = self._iso_week(ts)
        out: list[Intent] = []
        if self._week is None:
            self._week = wid
        elif wid != self._week:
            self._week = wid
            self.weeks += 1
            for s in live:
                px = self._week_close.get(s)
                if px is not None:
                    self._w[s].append(float(px))
            if any(len(self._w.get(s, [])) < self.n + 1 for s in live):
                out = [Intent(s, "FLAT", 0.0, reason="warmup") for s in live]
            else:
                lev_each = self.leverage / len(live)
                for s in live:
                    xs = self._w[s]
                    sma = sum(xs[-self.n :]) / self.n
                    c = xs[-1]
                    if c > sma:
                        side, reason = "LONG", "wsma+"
                    elif self.short_enabled:
                        side, reason = "SHORT", "wsma-"
                    else:
                        side, reason = "FLAT", "wsma-flat"
                    if side != self._last_side[s]:
                        self.flips += 1
                        self._last_side[s] = side
                        out.append(
                            Intent(
                                s,
                                side,
                                lev_each if side != "FLAT" else 0.0,
                                reason=reason,
                                hold_contracts=self.hold_contracts,
                            )
                        )
        for s in live:
            self._week_close[s] = float(closes[s])
        return out


class MonthlySma:
    """Faber 2007: month-end close vs 10-month SMA. Long or cash."""

    def __init__(
        self,
        symbols: list[str],
        n: int = 10,
        leverage: float = 2.0,
        hold_contracts: bool = True,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.n = int(n)
        self.leverage = float(leverage)
        self.hold_contracts = bool(hold_contracts)
        self._last_month: int | None = None
        self._m: dict[str, list[float]] = {s: [] for s in self.symbols}
        hc = "holdc" if hold_contracts else "rebal"
        self.strategy_id = f"cfm_faber{n}m_long_lev{leverage:g}_{hc}_" + "_".join(x.lower() for x in self.symbols)

    def on_close(self, ts: int, closes: dict[str, float], highs: dict, lows: dict) -> list[Intent]:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(ts / 1_000_000_000, tz=timezone.utc)
        live = [s for s in self.symbols if s in closes]
        if not live:
            return []
        month = dt.year * 12 + dt.month
        if self._last_month is None:
            self._last_month = month
            return []
        if month == self._last_month:
            return []
        # new month: previous bar was month-end close (this is first bar of new month)
        self._last_month = month
        for s in live:
            self._m[s].append(float(closes[s]))
        if any(len(self._m[s]) < self.n + 1 for s in live):
            return [Intent(s, "FLAT", 0.0, reason="warmup") for s in live]
        lev_each = self.leverage / len(live)
        out: list[Intent] = []
        for s in live:
            xs = self._m[s]
            sma = sum(xs[-self.n :]) / self.n
            c = xs[-1]
            if c > sma:
                out.append(Intent(s, "LONG", lev_each, reason="faber+", hold_contracts=self.hold_contracts))
            else:
                out.append(Intent(s, "FLAT", 0.0, reason="faber-flat", hold_contracts=self.hold_contracts))
        return out
