# stratforge

Create deterministic trading strategies with an AI agent, backtest them, compare them, and promote winning versions onto paper and live portfolios.

MIT license. A clone does not trade. Live orders need an API key, a portfolio id, and a `data/LIVE_APPROVED` file that you create. There is no silent portfolio default.

## What is in this repo

- Decision engine, public candles, paper simulator, and research replay.
- Order path: `ccxt_bridge`, `live_executor`, reconcile, buy-protect, `live_node`.
- Strategy families: trend rotation, Donchian, `short/` (`tsmom12_weekly`; bear-regime configs are rejected variants), `cfm/` (CDE nanos, isolated).
- Viz source. Snapshots and passwords are not in git.
- Generic systemd and nginx templates in `deploy/`. They use `%h` or a variable you set. They do not name a host.

## What stays on the machine that runs a book

Journals, the candle warehouse, `.env.live`, `data/LIVE_APPROVED`, halt files, viz snapshots, and htpasswd. Do not commit them.

## Run paper

```bash
git clone git@github.com:jag3773/stratforge.git
cd stratforge
uv venv --python 3.13 .venv
uv pip install -r requirements.txt
.venv/bin/python -m stratforge.live --config config/live_paper.json
```

Paper must not load API keys. `LIVE_TRADING` stays false.

## Promote to live

See `docs/PROMOTE.md`. Do not start live from this README.

Short identity is `config/short/tsmom12_weekly.json`, not `bear_regime_cover`.
