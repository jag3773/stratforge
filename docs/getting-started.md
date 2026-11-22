# Getting Started

## Prerequisites

- Linux with systemd (Debian/Ubuntu amd64 in production)
- Python **3.13** and [uv](https://docs.astral.sh/uv/)
- `git`, `curl`
- Coinbase **public** REST is enough for paper. LIVE needs a view+trade key (no withdraw) in `.env.live` mode 600

## Installation

```bash
git clone git@github.com:jag3773/stratforge.git
cd stratforge
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
scripts/smoke_check.sh
```

Do **not** install `nautilus_trader`. `requirements.txt` does not pin it.

## First run

```bash
mkdir -p data/raw logs
# copy parquet or: .venv/bin/python -u scripts/download_5m_top50.py
.venv/bin/python -u strategies/live_node.py --config config/live_paper.json
```

Expect `[node] requested_mode=paper`, a journal restore line, and `warmed indicators with N historical 4h bars`. Fills wait for the next **completed** 4h close (8pm / midnight / 4am / 8am / noon / 4pm ET; one hour earlier in winter).

## Common workflows

### Paper status / service

```bash
.venv/bin/python scripts/paper_status.py
systemctl --user status pro-trader-paper.service
```

### CASH research (production DNA)

```bash
.venv/bin/python scripts/run_portfolio_backtest.py \
  --config config/trend_rotation_25_max8_rotate.json \
  --start 2024-01-01 --end 2026-08-01
```

### Stagger birth window

```bash
.venv/bin/python scripts/staggered_backtest_status.py \
  --start 2026-07-02 --end 2026-08-02 --label m01 \
  --config config/trend_rotation_25_max8_rotate.json
```

### Tests

```bash
.venv/bin/python -m pytest tests/
scripts/smoke_check.sh   # real host gate
```

GitHub `ci-lite` runs DNA + `pytest tests/`.

## Configuration

- `config/trend_rotation_25_max8_rotate.json` — production DNA
- `config/live_paper.json` — paper overlay (`_include` DNA)
- `config/live_micro.json` — micro LIVE (`start_usd=250`, clip `$62.50`, daily halt `$25`)
- `config/universe_live.json` — admitted symbols. Watchlists are not read
- Halt: `data/TRADING_HALTED` or `data/TRADING_HALTED_{paper,live}`
- LIVE also needs `LIVE_TRADING=true`, `SIMULATION_MODE=false`, a **new** `data/LIVE_APPROVED`

## Next

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [README.md#module-map](README.md#module-map)
- [FRESH_VPS_SETUP.md](FRESH_VPS_SETUP.md) · [LIVE_READINESS.md](LIVE_READINESS.md)
