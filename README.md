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

## Install

The supported run path is the package:

```bash
pip install stratforge
```

A development checkout:

```bash
git clone git@github.com:jag3773/stratforge.git
cd stratforge
uv venv --python 3.13 .venv
uv pip install -e .
```

## State directory

Journals, configs, and `data/LIVE_APPROVED` live in the state directory. Set `STRATFORGE_STATE` to that directory before the process starts. If it is unset and the working directory contains `config/`, that directory is the state directory. Do not put a copy of `strategies/` or `ccxt_bridge.py` in the state directory. It is on `sys.path` and would shadow the package.

## Run paper

```bash
export STRATFORGE_STATE=/path/to/state
python -m stratforge.live --config config/live_paper.json
```

Paper must not load API keys. `LIVE_TRADING` stays false.

## Promote to live

See `docs/PROMOTE.md`. Do not start live from this README.

Short identity is `config/short/tsmom12_weekly.json`, not `bear_regime_cover`.

## Agent setup prompt

Paste this to an agent on a new machine. It starts paper only.

```text
Set up stratforge and start the paper book on this machine. Do not place live orders. Do not create data/LIVE_APPROVED. Do not set LIVE_TRADING=true. Do not put API keys in a unit file or in git.

1. This needs Linux, Python 3.13, and a systemd user session. Install uv if it is missing.
2. Clone https://github.com/jag3773/stratforge.git. That checkout is the state directory. It already contains config/.
3. In the checkout: uv venv --python 3.13 .venv && uv pip install -e .
4. Export STRATFORGE_STATE to that checkout before any stratforge import. Do not copy strategies/ or ccxt_bridge.py onto the checkout root. Those files inside the package are the ones that must run.
5. mkdir -p data/raw logs. Pull candles with the venv before expecting signals: STRATFORGE_STATE=$PWD .venv/bin/python -c "import stratforge, runpy; runpy.run_path('scripts/refresh_recent_5m.py')". If that fails, stop and report the error. Do not invent candles, and do not fix it by copying package files onto the checkout root.
6. Install a user systemd unit from deploy/stratforge-paper.service.example. WorkingDirectory, STRATFORGE_STATE, and the venv must be this checkout. ExecStart is the venv python -u -m stratforge.live --config config/live_paper.json. LIVE_TRADING=false. SIMULATION_MODE=true. Enable linger. Do not install the live unit.
7. Start the paper service. Done means the process command is -m stratforge.live, the log shows requested_mode=paper, and the journal is data/execution_log.jsonl in this checkout. Fills wait for the next closed 4h bar. An empty book before that close is expected.
8. Report the checkout path, the unit name, and the log lines that prove paper mode. Stop. Ask before any live step.
```
