"""Apply one engine bar through a PaperExecutor."""
from __future__ import annotations


def apply_engine_bar(engine, executor, symbol, bar, *, apply_enabled=True) -> list[dict]:
    symbol = symbol.upper()
    price = float(bar["close"])
    executor.update_price(symbol, price)
    acts = engine.on_bar(symbol, bar, emit=True, positions=executor.positions)
    prepared = [{**a, "price": a.get("price", price)} for a in acts]
    if prepared and apply_enabled:
        executor.apply(prepared)
    engine.sync_slots_from_positions(executor.positions)
    return prepared


def apply_family_bar(engine, executor, symbol, bar, *, apply_enabled=True) -> list[dict]:
    """FamilyEngine path (donch paper soak). Does not touch SignalEngine."""
    symbol = symbol.upper()
    price = float(bar["close"])
    ts = int(bar["ts"])
    executor.update_price(symbol, price)
    if not apply_enabled:
        engine.update(symbol, bar)
        engine.sync_from_positions(executor.positions)
        return []
    acts = engine.on_bar_stream(symbol, bar, ts)
    prepared = [{**a, "price": a.get("price", price)} for a in acts]
    if prepared:
        executor.apply(prepared)
    engine.sync_from_positions(executor.positions)
    for s, p in executor.positions.items():
        engine.entry_px[s] = float(p.get("entry") or 0)
        if s not in engine.entry_ts:
            engine.entry_ts[s] = ts
    return prepared
