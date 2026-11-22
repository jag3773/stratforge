# Phase 0 — single runtime (`.venv`)

**Status:** Done 2026-08-08 (env name still `.venv`)  
**Goal:** One Python environment for paper ops, research, admission, and cron — no dual-venv kludge.

Nautilus is **historical / archived** (`archive/thin_host_nautilus/`). Paper is **REST + SignalEngine**.

## What “Phase 0” means

| Is | Is not |
|---|---|
| Research/backtests run on the **same** stack as paper (`.venv`, `requirements.txt`) | Installing Nautilus or porting the old 1.221 `BacktestEngine` harness |
| Decision-faithful backtests via **SignalEngine + CASH PaperExecutor** | Running known-broken dual equity paths |
| v1 Strategy harnesses **archived**; Nautilus 2.x costume **archived** | Keeping `.venv` (1.221) or LiveNode as a supported runtime |

## Canonical paths

| Concern | Path |
|---|---|
| Runtime | `.venv` only (`requirements.txt`; no Nautilus pin) |
| Decisions | `strategies/signal_engine.py` via `apply_engine_bar` (REST 5m→4h) |
| Research/backtest | `strategies/research_replay.py` |
| Portfolio CLI | `scripts/run_portfolio_backtest.py` |
| Stagger CLI | `scripts/staggered_backtest_status.py` |
| Admission | `scripts/admission/*` → re-exports research replay |
| Paper node | `python -m stratforge.live` (not `strategies/live_node.py`) |
| Nautilus costume | `archive/thin_host_nautilus/` |
| Legacy 1.221 harnesses | `archive/strategies_v1_backtest/` |
| Legacy venv (if present on disk) | `.venv-v1-archived` — do not use |

## Why not native Nautilus `BacktestEngine` for DNA

Production DNA lives in framework-agnostic `SignalEngine`. Research stays on
`research_replay` (CASH). Nautilus is not on the operator path.

## Paper / live host (2026-08-23)

Paper is REST 5m fold → `apply_engine_bar` → SignalEngine → journaled executor.
The old `TrendRotationV2Strategy` LiveNode costume is archived.

**Still true:**
- Research economics = `research_replay` (CASH PaperExecutor)
- Paper / LIVE **fills** = journaled executors
- Fill-level BacktestEngine vs replay is **not** claimed
- Persistence of candles/journals: [DATA_AND_PERSISTENCE.md](DATA_AND_PERSISTENCE.md)

## Commands

```bash
# run from the checkout

# Unit / parity
.venv/bin/python scripts/test_paper_executor.py
.venv/bin/python scripts/test_signal_parity.py
.venv/bin/python scripts/test_admission.py
.venv/bin/python -m pytest tests/test_thin_host_cutover.py tests/test_phase2_execution.py tests/test_p0_live_safety.py
# or: scripts/smoke_check.sh

# Single window portfolio
.venv/bin/python scripts/run_portfolio_backtest.py \
  --config config/trend_rotation_25_max8_rotate.json \
  --start 2024-01-01 --end 2026-08-01

# Stagger birth (CASH, production DNA default)
.venv/bin/python scripts/staggered_backtest_status.py \
  --start 2026-07-02 --end 2026-08-02 --label m01 \
  --config config/trend_rotation_25_max8_rotate.json
```

## Spot economics

Always **CASH** via PaperExecutor. Equity = cash + Σ(qty × last mark).

## Recreate runtime

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
scripts/smoke_check.sh
```

See also: [ARCHITECTURE.md](ARCHITECTURE.md) · [getting-started.md](getting-started.md) · [PROMOTE.md](PROMOTE.md)
