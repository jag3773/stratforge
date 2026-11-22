# strategies/

| File | Role |
|---|---|
| `signal_engine.py` | **Decision SSOT** (`SignalConfig.from_dict` for JSON knobs) |
| `config_load.py` | JSON `_include` resolver (live overlays inherit DNA) |
| `research_replay.py` | **Research/backtest SSOT** — SignalEngine + CASH PaperExecutor (`.venv`) |
| `live_node.py` | PAPER/LIVE orchestrator (REST + `apply_engine_bar`) |
| `live_data_feed.py` | Public REST 5m → 4h |
| `paper_executor.py` | Simulated fills + journal restore |
| `live_executor.py` | LIVE via `ccxt_bridge` (dry-run path used in tests) |
| `universe.py` | Admitted universe loader (`config/universe_live.json`) |
| `node_lock.py` | Per-mode flock (paper/live) |
| `bar_freshness.py` | Skip apply on stale/future 4h bars |
| `day_risk.py` | UTC-day MTM loss tracker |
| `live_reconcile.py` | LIVE book vs exchange (fail-closed) |
| `qty_precision.py` | `floor_qty` for residual / partial fills; `clip_sell_qty` so LIVE closes never exceed wallet available |

**Backtest CLI:** `scripts/run_portfolio_backtest.py`, `scripts/staggered_backtest_status.py`  
**Archived v1 Nautilus Strategy harnesses:** [`../archive/strategies_v1_backtest/`](../archive/strategies_v1_backtest/)  
**Archived Nautilus 2.x costume:** [`../archive/thin_host_nautilus/`](../archive/thin_host_nautilus/)  
**Archived unused scaffold:** [`../archive/strategies/v2_live_node_scaffold.py`](../archive/strategies/v2_live_node_scaffold.py)  
Docs: [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) · [../docs/PHASE0_V2_RUNTIME.md](../docs/PHASE0_V2_RUNTIME.md)
