# Isolated short book — `tsmom12_weekly`

**This is not `trend_rotation_25`.** Different identity, files, ledger, journals, and (later) portfolio.

Long paper / `strategies/signal_engine.py` / the live sleeve spot book stay untouched.

## Identity

| | |
|---|---|
| research SSOT | `tsmom12_weekly` |
| 4h-EMA baseline | `bear_regime_cover` (kept, not the lead) |
| venue | Coinbase Financial Markets **US perpetual-style futures** |
| universe | **BTC + ETH only** (nano BIP / ETP) |
| account | `MARGIN_1X` (research never uses 10×) |
| stance | 12-month TSMOM < 0 (weekly); **cash in bulls / 2024 chop** |
| stop | none (no ATR, no cat stop) |
| naive invert | **not built** |

## Isolation

```
config/short/          configs (not config/live_paper.json)
short/                 engine, ledger, replay, tests
results/backtests/short/
data/short/            reserved for a future paper journal
```

`short/` must not import `strategies.signal_engine`, `paper_executor`, or `research_replay`. Guard: `short/test_short.py`.

## Research command

```
# run from the checkout
.venv/bin/python short/test_tsmom.py
.venv/bin/python short/run_tsmom12.py
.venv/bin/python short/test_short.py
.venv/bin/python short/run_backtest.py   # bear_regime_cover baseline
```

Price proxy: Coinbase **spot** 5m → 4h. Basis vs BIP/ETP unmodeled. Funding is a **charge to shorts only** (never a credit).

## Safety

Paper/research only. No live CFM orders. No shared systemd with the long node.
Full story: [../docs/SHORT_STRATEGY.md](../docs/SHORT_STRATEGY.md)
