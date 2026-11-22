# short

Isolated from the spot long book. Do not import `strategies.signal_engine`, `paper_executor`, or `research_replay`.

The identity that ships is `config/short/tsmom12_weekly.json`.

- Weekly 12-month time-series momentum, BTC and ETH, 1x
- Short a name only while its 12-month return is negative
- No ATR stop
- Not mixed with the spot journal or portfolio

`config/short/bear_regime_*.json` are rejected variants. Do not run them as the book.

```bash
python -m stratforge.short.test_tsmom
python -m stratforge.short.run_tsmom12
```
