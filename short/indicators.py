"""Minimal indicators for the isolated short stack. Not imported from SignalEngine."""

from __future__ import annotations


class EMA:
    """SMA-seeded EMA. initialized only after `period` bars (honest warmup)."""

    def __init__(self, period: int):
        self.period = int(period)
        self._alpha = 2.0 / (self.period + 1.0)
        self._sum = 0.0
        self._n = 0
        self.value = 0.0
        self.initialized = False

    def handle_bar(self, price: float) -> None:
        px = float(price)
        self._n += 1
        if not self.initialized:
            self._sum += px
            if self._n >= self.period:
                self.value = self._sum / self.period
                self.initialized = True
            return
        self.value = self._alpha * px + (1.0 - self._alpha) * self.value


class ATR:
    """Wilder ATR. initialized after `period` true ranges."""

    def __init__(self, period: int = 14):
        self.period = int(period)
        self._prev_close: float | None = None
        self._atr = 0.0
        self._n = 0
        self.value = 0.0
        self.initialized = False

    def handle_bar(self, high: float, low: float, close: float) -> None:
        h, l, c = float(high), float(low), float(close)
        if self._prev_close is None:
            tr = h - l
            self._prev_close = c
        else:
            pc = self._prev_close
            tr = max(h - l, abs(h - pc), abs(l - pc))
            self._prev_close = c
        if not self.initialized:
            self._atr += tr
            self._n += 1
            if self._n >= self.period:
                self._atr /= self.period
                self.value = self._atr
                self.initialized = True
            return
        self._atr = (self._atr * (self.period - 1) + tr) / self.period
        self.value = self._atr
