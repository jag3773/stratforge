"""CFM/short ledger sizing, daily warmup, and replay state sync."""
from __future__ import annotations

from stratforge.cfm.ledger import FuturesLedger
from stratforge.cfm.replay import merge_intents, note_ledger_closed
from stratforge.cfm.strats import DonchianTurtle, Intent
from stratforge.cfm.strats_cde import ChandelierLong
from stratforge.short.engine import BearConfig, BearRegimeEngine
from stratforge.short.ledger import ShortLedger


def test_futures_open_does_not_drive_available_negative():
    led = FuturesLedger(start_usd=1000.0, max_leverage=4.0, fee_rate=0.006)
    rec = led.open_pos("BTC", "LONG", 100.0, 1, 100_000.0, "t")
    assert rec is not None
    assert led.available() >= -1e-6
    assert rec["qty"] * rec["price"] > 1000.0


def test_short_size_respects_leverage():
    led = ShortLedger(start_usd=1000.0, max_leverage=4.0, fee_rate=0.001, integer_contracts=False)
    qty = led.size_qty("BTC", 100.0, 100_000.0)
    assert qty * 100.0 > 1500.0
    rec = led.short("BTC", 100.0, 1, 100_000.0, "t")
    assert rec is not None
    assert led.available() >= -1e-6


def test_daily_ema_updates_before_4h_ema_ready():
    eng = BearRegimeEngine(
        BearConfig(slow_ema_period=200, atr_period=50, daily_ema_period=2),
        ["BTC"],
    )
    day = 1_700_000_000
    for i in range(3):
        ts = (day + i * 86400) * 1_000_000_000
        eng.on_bar("BTC", {"ts": ts, "open": 100, "high": 101, "low": 99, "close": 100 + i}, emit=False)
    assert eng.states["BTC"].ema.initialized is False
    assert eng.daily_ema is not None
    assert eng.daily_ema._n >= 1


def test_merge_intents_keeps_earlier_symbol():
    first = [Intent("BTC", "LONG", 1.0, reason="a")]
    second = [Intent("ETH", "LONG", 1.0, reason="b")]
    merged = merge_intents(first, second)
    sides = {it.symbol: it.side for it in merged}
    assert sides == {"BTC": "LONG", "ETH": "LONG"}


def test_note_closed_clears_turtle_and_chandelier():
    turtle = DonchianTurtle(["BTC", "ETH"], entry_n=2, exit_n=2)
    turtle._side["BTC"] = "LONG"
    turtle._last_sent["BTC"] = "LONG"
    note_ledger_closed(turtle, {"BTC"})
    assert turtle._side["BTC"] == "FLAT"
    assert turtle._last_sent["BTC"] == "FLAT"

    ch = ChandelierLong()
    ch._last = "LONG"
    note_ledger_closed(ch, {"BTC"})
    assert ch._last == "FLAT"
