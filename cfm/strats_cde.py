"""Additional CFM identities for the short CDE tape. Not trend_rotation.

Each class is a distinct rule family. Signals on closed bars; replay fills next open.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from cfm.strats import Intent, _atr_step, _ema


def _iso_week(ts: int) -> int:
    ic = datetime.fromtimestamp(ts / 1_000_000_000, tz=timezone.utc).isocalendar()
    return int(ic.year) * 100 + int(ic.week)


def _rsi(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 2:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses <= 1e-12:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def _wma(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    w = list(range(1, n + 1))
    s = sum(w)
    return sum(xs[-n + i] * w[i] for i in range(n)) / s


class _Weeker:
    def __init__(self, symbols: list[str]):
        self.symbols = [s.upper() for s in symbols]
        self._week: int | None = None
        self._week_close: dict[str, float] = {}
        self._week_high: dict[str, float] = {}
        self._week_low: dict[str, float] = {}
        self.w_c: dict[str, list[float]] = {s: [] for s in self.symbols}
        self.w_h: dict[str, list[float]] = {s: [] for s in self.symbols}
        self.w_l: dict[str, list[float]] = {s: [] for s in self.symbols}
        self.weeks = 0
        self.flips = 0
        self._last: dict[str, str] = {s: "FLAT" for s in self.symbols}

    def roll(self, ts: int, closes: dict, highs: dict, lows: dict) -> bool:
        """Update weekly series. Return True on a newly completed week."""
        live = [s for s in self.symbols if s in closes]
        if not live:
            return False
        wid = _iso_week(ts)
        new = False
        if self._week is None:
            self._week = wid
        elif wid != self._week:
            self._week = wid
            self.weeks += 1
            new = True
            for s in live:
                if s in self._week_close:
                    self.w_c[s].append(self._week_close[s])
                    self.w_h[s].append(self._week_high.get(s, self._week_close[s]))
                    self.w_l[s].append(self._week_low.get(s, self._week_close[s]))
            for s in live:
                self._week_high[s] = float(highs.get(s, closes[s]))
                self._week_low[s] = float(lows.get(s, closes[s]))
        for s in live:
            c, h, l = float(closes[s]), float(highs.get(s, closes[s])), float(lows.get(s, closes[s]))
            self._week_close[s] = c
            self._week_high[s] = max(self._week_high.get(s, h), h)
            self._week_low[s] = min(self._week_low.get(s, l), l)
        return new

    def emit(self, sym: str, side: str, lev: float, reason: str, hold: bool = True) -> list[Intent]:
        if side == self._last[sym]:
            return []
        self.flips += 1
        self._last[sym] = side
        return [Intent(sym, side, lev if side != "FLAT" else 0.0, reason=reason, hold_contracts=hold)]


class TsmomWeeks(_Weeker):
    """Weekly close vs close N weeks ago. Moskowitz-style, short lookback for CDE tape."""

    def __init__(self, n_weeks: int, leverage: float, short: bool = False):
        super().__init__(["BTC"])
        self.n = int(n_weeks)
        self.leverage = float(leverage)
        self.short = bool(short)
        self.strategy_id = f"cfm_tsmom{n_weeks}w_{'ls' if short else 'long'}_lev{leverage:g}_btc"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        xs = self.w_c["BTC"]
        if len(xs) < self.n + 1:
            return self.emit("BTC", "FLAT", 0, "warmup")
        ret = xs[-1] / xs[-1 - self.n] - 1.0
        if ret > 0:
            return self.emit("BTC", "LONG", self.leverage, "mom+")
        if self.short:
            return self.emit("BTC", "SHORT", self.leverage, "mom-")
        return self.emit("BTC", "FLAT", 0, "mom-flat")


class TripleWeeklyMA(_Weeker):
    """Classic 4/9/18 weekly SMA stack. Long only when 4>9>18."""

    def __init__(self, leverage: float = 3.0):
        super().__init__(["BTC"])
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_triple_ma_4_9_18_lev{leverage:g}_btc"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        xs = self.w_c["BTC"]
        if len(xs) < 19:
            return self.emit("BTC", "FLAT", 0, "warmup")
        a = sum(xs[-4:]) / 4
        b = sum(xs[-9:]) / 9
        c = sum(xs[-18:]) / 18
        if a > b > c:
            return self.emit("BTC", "LONG", self.leverage, "stack")
        return self.emit("BTC", "FLAT", 0, "unstack")


class DonchianWeeks(_Weeker):
    """Weekly Donchian: enter N-week high, exit M-week low. Turtle on weeks."""

    def __init__(self, entry: int = 20, exit_n: int = 10, leverage: float = 3.0):
        super().__init__(["BTC"])
        self.entry, self.exit_n = int(entry), int(exit_n)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_donch_w{entry}_{exit_n}_lev{leverage:g}_btc"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        need = max(self.entry, self.exit_n) + 1
        if len(self.w_c["BTC"]) < need:
            return self.emit("BTC", "FLAT", 0, "warmup")
        hh = max(self.w_h["BTC"][-self.entry - 1 : -1])
        xl = min(self.w_l["BTC"][-self.exit_n - 1 : -1])
        c = self.w_c["BTC"][-1]
        side = self._last["BTC"]
        if side != "LONG" and c > hh:
            return self.emit("BTC", "LONG", self.leverage, "wbreak")
        if side == "LONG" and c < xl:
            return self.emit("BTC", "FLAT", 0, "wexit")
        return []


class RocWeeks(_Weeker):
    def __init__(self, n: int = 12, leverage: float = 3.0):
        super().__init__(["BTC"])
        self.n = int(n)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_roc{n}w_long_lev{leverage:g}_btc"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        xs = self.w_c["BTC"]
        if len(xs) < self.n + 1:
            return self.emit("BTC", "FLAT", 0, "warmup")
        if xs[-1] > xs[-1 - self.n]:
            return self.emit("BTC", "LONG", self.leverage, "roc+")
        return self.emit("BTC", "FLAT", 0, "roc-")


class SeasonNovApr:
    """Crypto 'winter' heuristic: long Nov–Apr, flat May–Oct."""

    def __init__(self, leverage: float = 2.0):
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_season_nov_apr_lev{leverage:g}_btc"
        self._last = "FLAT"
        self.flips = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        mo = datetime.fromtimestamp(ts / 1_000_000_000, tz=timezone.utc).month
        side = "LONG" if mo in (11, 12, 1, 2, 3, 4) else "FLAT"
        if side == self._last:
            return []
        self.flips += 1
        self._last = side
        return [Intent("BTC", side, self.leverage if side == "LONG" else 0.0, reason=f"m{mo}", hold_contracts=True)]


class IchimokuTJ:
    """Tenkan/Kijun cross + close vs Kijun. Periods in 4h bars (9d/26d)."""

    def __init__(self, tenkan: int = 54, kijun: int = 156, leverage: float = 3.0):
        self.t, self.k = int(tenkan), int(kijun)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_ichimoku_{tenkan}_{kijun}_lev{leverage:g}_btc"
        self.h: list[float] = []
        self.l: list[float] = []
        self.c: list[float] = []
        self._last = "FLAT"
        self.flips = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        self.h.append(float(highs["BTC"]))
        self.l.append(float(lows["BTC"]))
        self.c.append(float(closes["BTC"]))
        if len(self.c) < self.k + 2:
            return []
        ten = (max(self.h[-self.t :]) + min(self.l[-self.t :])) / 2
        kij = (max(self.h[-self.k :]) + min(self.l[-self.k :])) / 2
        px = self.c[-1]
        side = "LONG" if ten > kij and px > kij else "FLAT"
        if side == self._last:
            return []
        self.flips += 1
        self._last = side
        return [Intent("BTC", side, self.leverage if side == "LONG" else 0.0, reason="ichi", hold_contracts=True)]


class Supertrend:
    def __init__(self, atr_n: int = 10, mult: float = 3.0, leverage: float = 3.0):
        self.atr_n = int(atr_n)
        self.mult = float(mult)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_supertrend_{atr_n}_{mult:g}_lev{leverage:g}_btc"
        self.atr = None
        self.prev_c = None
        self.up = None
        self.dn = None
        self.trend = 1
        self._last = "FLAT"
        self.flips = 0
        self.n = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        h, l, c = float(highs["BTC"]), float(lows["BTC"]), float(closes["BTC"])
        pc = self.prev_c if self.prev_c is not None else c
        self.atr = _atr_step(self.atr, h, l, pc, self.atr_n)
        self.prev_c = c
        self.n += 1
        if self.n < self.atr_n + 2 or not self.atr:
            return []
        mid = (h + l) / 2
        bu = mid + self.mult * self.atr
        bl = mid - self.mult * self.atr
        if self.up is None or self.dn is None:
            self.up, self.dn = bl, bu
        up = float(self.up)
        dn = float(self.dn)
        if c > up:
            self.trend = 1
        elif c < dn:
            self.trend = -1
        if self.trend == 1:
            self.up = max(bl, up)
            self.dn = bu
        else:
            self.dn = min(bu, dn)
            self.up = bl
        side = "LONG" if self.trend == 1 else "FLAT"
        if side == self._last:
            return []
        self.flips += 1
        self._last = side
        return [Intent("BTC", side, self.leverage if side == "LONG" else 0.0, reason="st", hold_contracts=True)]


class BollBreak:
    """Donchian-like: close above 20-bar 2σ band → long; back to mid → flat."""

    def __init__(self, n: int = 20, k: float = 2.0, leverage: float = 3.0):
        self.n, self.k = int(n), float(k)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_boll_break_{n}_{k:g}_lev{leverage:g}_btc"
        self.c: list[float] = []
        self._last = "FLAT"
        self.flips = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        self.c.append(float(closes["BTC"]))
        if len(self.c) < self.n + 2:
            return []
        w = self.c[-self.n :]
        m = sum(w) / self.n
        var = sum((x - m) ** 2 for x in w) / self.n
        sd = math.sqrt(max(var, 0.0))
        px = self.c[-1]
        upper, mid = m + self.k * sd, m
        if self._last != "LONG" and px > upper:
            self.flips += 1
            self._last = "LONG"
            return [Intent("BTC", "LONG", self.leverage, reason="bb_up", hold_contracts=True)]
        if self._last == "LONG" and px < mid:
            self.flips += 1
            self._last = "FLAT"
            return [Intent("BTC", "FLAT", 0.0, reason="bb_mid", hold_contracts=True)]
        return []


class BollMeanRev:
    """Fade 2σ with 50-bar SMA trend filter. Exit at mid."""

    def __init__(self, n: int = 20, k: float = 2.0, sma: int = 50, leverage: float = 2.0):
        self.n, self.k, self.sma = int(n), float(k), int(sma)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_boll_mr_{n}_{k:g}_sma{sma}_lev{leverage:g}_btc"
        self.c: list[float] = []
        self._last = "FLAT"
        self.flips = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        self.c.append(float(closes["BTC"]))
        need = max(self.n, self.sma) + 2
        if len(self.c) < need:
            return []
        w = self.c[-self.n :]
        m = sum(w) / self.n
        sd = math.sqrt(max(sum((x - m) ** 2 for x in w) / self.n, 0.0))
        trend = sum(self.c[-self.sma :]) / self.sma
        px = self.c[-1]
        lower = m - self.k * sd
        if self._last != "LONG" and px < lower and px > trend:
            self.flips += 1
            self._last = "LONG"
            return [Intent("BTC", "LONG", self.leverage, reason="fade", hold_contracts=True)]
        if self._last == "LONG" and px >= m:
            self.flips += 1
            self._last = "FLAT"
            return [Intent("BTC", "FLAT", 0.0, reason="mid", hold_contracts=True)]
        return []


class ConnorsRsi2:
    """RSI(2) < 10 and close > SMA(200 4h) → long; RSI>70 exit."""

    def __init__(self, leverage: float = 2.0):
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_connors_rsi2_sma200_lev{leverage:g}_btc"
        self.c: list[float] = []
        self._last = "FLAT"
        self.flips = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        self.c.append(float(closes["BTC"]))
        if len(self.c) < 210:
            return []
        rsi = _rsi(self.c, 2)
        sma = sum(self.c[-200:]) / 200
        px = self.c[-1]
        if rsi is None:
            return []
        if self._last != "LONG" and rsi < 10 and px > sma:
            self.flips += 1
            self._last = "LONG"
            return [Intent("BTC", "LONG", self.leverage, reason="crsi", hold_contracts=True)]
        if self._last == "LONG" and rsi > 70:
            self.flips += 1
            self._last = "FLAT"
            return [Intent("BTC", "FLAT", 0.0, reason="crsi_x", hold_contracts=True)]
        return []


class HullTrend:
    def __init__(self, n: int = 32, leverage: float = 3.0):
        self.n = int(n)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_hull_{n}_lev{leverage:g}_btc"
        self.c: list[float] = []
        self._last = "FLAT"
        self.flips = 0
        self._prev = None

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        self.c.append(float(closes["BTC"]))
        n = self.n
        n2 = max(n // 2, 1)
        ns = max(int(math.sqrt(n)), 1)
        if len(self.c) < n + ns + 2:
            return []
        wma_n = _wma(self.c, n)
        wma_h = _wma(self.c, n2)
        if wma_n is None or wma_h is None:
            return []
        # store raw hull series via trailing window of (2*wma_h - wma_n) approx using last ns of that
        raw = 2 * wma_h - wma_n
        if not hasattr(self, "_raw"):
            self._raw: list[float] = []
        self._raw.append(raw)
        hma = _wma(self._raw, ns)
        if hma is None or self._prev is None:
            self._prev = hma
            return []
        side = "LONG" if hma > self._prev else "FLAT"
        self._prev = hma
        if side == self._last:
            return []
        self.flips += 1
        self._last = side
        return [Intent("BTC", side, self.leverage if side == "LONG" else 0.0, reason="hull", hold_contracts=True)]


class MacdCross:
    def __init__(self, fast: int = 12, slow: int = 26, sig: int = 9, leverage: float = 3.0):
        self.fast, self.slow, self.sig = int(fast), int(slow), int(sig)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_macd_{fast}_{slow}_{sig}_lev{leverage:g}_btc"
        self.ef = self.es = self.e_sig = None
        self.n = 0
        self._last = "FLAT"
        self.flips = 0
        self.prev_hist = None

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        c = float(closes["BTC"])
        self.ef = _ema(self.ef, c, self.fast)
        self.es = _ema(self.es, c, self.slow)
        self.n += 1
        if self.n < self.slow + self.sig:
            return []
        macd = self.ef - self.es
        self.e_sig = _ema(self.e_sig, macd, self.sig)
        hist = macd - self.e_sig
        prev = self.prev_hist
        self.prev_hist = hist
        if prev is None:
            return []
        side = self._last
        if prev <= 0 < hist:
            side = "LONG"
        elif prev >= 0 > hist:
            side = "FLAT"
        if side == self._last:
            return []
        self.flips += 1
        self._last = side
        return [Intent("BTC", side, self.leverage if side == "LONG" else 0.0, reason="macd", hold_contracts=True)]


class KeltnerBreak:
    def __init__(self, n: int = 20, atr_n: int = 20, k: float = 2.0, leverage: float = 3.0):
        self.n, self.atr_n, self.k = int(n), int(atr_n), float(k)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_keltner_{n}_{k:g}_lev{leverage:g}_btc"
        self.ema = None
        self.atr = None
        self.prev_c = None
        self._last = "FLAT"
        self.flips = 0
        self.bars = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        h, l, c = float(highs["BTC"]), float(lows["BTC"]), float(closes["BTC"])
        self.ema = _ema(self.ema, c, self.n)
        pc = self.prev_c if self.prev_c is not None else c
        self.atr = _atr_step(self.atr, h, l, pc, self.atr_n)
        self.prev_c = c
        self.bars += 1
        if self.bars < max(self.n, self.atr_n) + 2 or self.ema is None or not self.atr:
            return []
        up = self.ema + self.k * self.atr
        if self._last != "LONG" and c > up:
            self.flips += 1
            self._last = "LONG"
            return [Intent("BTC", "LONG", self.leverage, reason="kel_up", hold_contracts=True)]
        if self._last == "LONG" and c < self.ema:
            self.flips += 1
            self._last = "FLAT"
            return [Intent("BTC", "FLAT", 0.0, reason="kel_x", hold_contracts=True)]
        return []


class ChandelierLong:
    """Long when close > N-bar high minus atr_mult*ATR; trail. Enter on 20-bar high."""

    def __init__(self, n: int = 22, atr_n: int = 22, k: float = 3.0, leverage: float = 3.0):
        self.n, self.atr_n, self.k = int(n), int(atr_n), float(k)
        self.leverage = float(leverage)
        self.strategy_id = f"cfm_chandelier_{n}_{k:g}_lev{leverage:g}_btc"
        self.h: list[float] = []
        self.c: list[float] = []
        self.atr = None
        self.prev_c = None
        self._last = "FLAT"
        self.flips = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        h, l, c = float(highs["BTC"]), float(lows["BTC"]), float(closes["BTC"])
        pc = self.prev_c if self.prev_c is not None else c
        self.atr = _atr_step(self.atr, h, l, pc, self.atr_n)
        self.prev_c = c
        self.h.append(h)
        self.c.append(c)
        if len(self.c) < max(self.n, self.atr_n) + 3 or not self.atr:
            return []
        hh = max(self.h[-self.n :])
        stop = hh - self.k * self.atr
        if self._last != "LONG" and c >= max(self.h[-self.n - 1 : -1]):
            self.flips += 1
            self._last = "LONG"
            return [Intent("BTC", "LONG", self.leverage, reason="ch_in", stop_px=stop, hold_contracts=True)]
        if self._last == "LONG" and c < stop:
            self.flips += 1
            self._last = "FLAT"
            return [Intent("BTC", "FLAT", 0.0, reason="ch_x", hold_contracts=True)]
        return []


class DualBtcEthMom(_Weeker):
    """12-week TSMOM on BTC and ETH, 1.5x each if both green else only the green one (cap 3x)."""

    def __init__(self, n: int = 12, lev_each: float = 1.5):
        super().__init__(["BTC", "ETH"])
        self.n = int(n)
        self.lev_each = float(lev_each)
        self.strategy_id = f"cfm_dual_tsmom{n}w_lev{lev_each:g}_btc_eth"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        out: list[Intent] = []
        for s in self.symbols:
            xs = self.w_c[s]
            if len(xs) < self.n + 1:
                out.extend(self.emit(s, "FLAT", 0, "warmup"))
                continue
            if xs[-1] > xs[-1 - self.n]:
                out.extend(self.emit(s, "LONG", self.lev_each, "mom+"))
            else:
                out.extend(self.emit(s, "FLAT", 0, "mom-"))
        return out


class AtrVolBreak:
    """Inside-bar squeeze then close beyond 1.5*ATR of prior. Direction of close vs open."""

    def __init__(self, atr_n: int = 14, k: float = 1.5, leverage: float = 2.0, hold_bars: int = 12):
        self.atr_n, self.k = int(atr_n), float(k)
        self.leverage = float(leverage)
        self.hold_bars = int(hold_bars)
        self.strategy_id = f"cfm_atr_volbreak_{atr_n}_{k:g}_lev{leverage:g}_btc"
        self.atr = None
        self.prev_c = None
        self.left = 0
        self._last = "FLAT"
        self.flips = 0
        self.n = 0

    def on_close(self, ts, closes, highs, lows):
        if "BTC" not in closes:
            return []
        o = float(closes["BTC"])  # no open passed; use prior close as proxy
        h, l, c = float(highs["BTC"]), float(lows["BTC"]), float(closes["BTC"])
        pc = self.prev_c if self.prev_c is not None else c
        self.atr = _atr_step(self.atr, h, l, pc, self.atr_n)
        rng = h - l
        self.n += 1
        if self.n < self.atr_n + 3 or not self.atr:
            self.prev_c = c
            return []
        want = self._last
        if self.left > 0:
            self.left -= 1
            if self.left == 0:
                want = "FLAT"
        elif rng > self.k * self.atr and c > pc:
            want = "LONG"
            self.left = self.hold_bars
        self.prev_c = c
        if want == self._last:
            return []
        self.flips += 1
        self._last = want
        return [Intent("BTC", want, self.leverage if want == "LONG" else 0.0, reason="vb", hold_contracts=True)]


class CrossSectionMom(_Weeker):
    """Weekly cross-sectional TSMOM. Long top N, optional short bottom N. Not trend_rotation."""

    def __init__(
        self,
        symbols: list[str],
        lookback: int = 12,
        n_long: int = 4,
        n_short: int = 0,
        lev_each: float = 0.5,
    ):
        super().__init__(symbols)
        self.lookback = int(lookback)
        self.n_long = int(n_long)
        self.n_short = int(n_short)
        self.lev_each = float(lev_each)
        ls = f"l{n_long}_s{n_short}"
        self.strategy_id = f"cfm_cs_tsmom{lookback}w_{ls}_lev{lev_each:g}_n{len(symbols)}"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        ranked: list[tuple[float, str]] = []
        for s in self.symbols:
            xs = self.w_c[s]
            if len(xs) >= self.lookback + 1 and xs[-1 - self.lookback] > 0:
                ranked.append((xs[-1] / xs[-1 - self.lookback] - 1.0, s))
        ranked.sort(reverse=True)
        want: dict[str, tuple[str, float]] = {}
        for _, s in ranked[: self.n_long]:
            want[s] = ("LONG", self.lev_each)
        if self.n_short > 0 and ranked:
            for _, s in ranked[-self.n_short :]:
                if s not in want:
                    want[s] = ("SHORT", self.lev_each)
        out: list[Intent] = []
        for s in self.symbols:
            if s in want:
                out.extend(self.emit(s, want[s][0], want[s][1], "cs"))
            else:
                out.extend(self.emit(s, "FLAT", 0.0, "cs_off"))
        return out


class TsmomCapLong(_Weeker):
    """Long every name with 12w return > 0, cap K by rank, equal lev."""

    def __init__(self, symbols: list[str], lookback: int = 12, max_n: int = 8, lev_each: float = 0.375):
        super().__init__(symbols)
        self.lookback = int(lookback)
        self.max_n = int(max_n)
        self.lev_each = float(lev_each)
        self.strategy_id = f"cfm_tsmom{lookback}w_cap{max_n}_lev{lev_each:g}_n{len(symbols)}"

    def on_close(self, ts, closes, highs, lows):
        if not self.roll(ts, closes, highs, lows):
            return []
        pos: list[tuple[float, str]] = []
        for s in self.symbols:
            xs = self.w_c[s]
            if len(xs) >= self.lookback + 1 and xs[-1 - self.lookback] > 0:
                ret = xs[-1] / xs[-1 - self.lookback] - 1.0
                if ret > 0:
                    pos.append((ret, s))
        pos.sort(reverse=True)
        keep = {s for _, s in pos[: self.max_n]}
        out: list[Intent] = []
        for s in self.symbols:
            if s in keep:
                out.extend(self.emit(s, "LONG", self.lev_each, "tm+"))
            else:
                out.extend(self.emit(s, "FLAT", 0.0, "tm-"))
        return out
