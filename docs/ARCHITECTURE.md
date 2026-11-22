# Architecture

[Getting started](getting-started.md) · [Promote](PROMOTE.md)

The process is `python -m stratforge.live`. Decision code lives in the `stratforge` package (`stratforge/strategies/`, `stratforge/ccxt_bridge.py`, `stratforge/risk_limits.py`). Journals and `data/LIVE_APPROVED` live in `STRATFORGE_STATE`, not beside the package.

Data enters as Coinbase public 5m candles (parquet at boot, REST after). Closed 4h bars (8pm / midnight / 4am / 8am / noon / 4pm ET) go through `apply_engine_bar` → `SignalEngine` → a journaled executor. Paper never loads API keys. LIVE is the same wiring with `LiveExecutor` + `ccxt_bridge`, and is **halted** until a new `data/LIVE_APPROVED`.

The short book (`stratforge/short/`) does not share this engine or journal. CFM long research is `stratforge/cfm/` (not this engine, not a rotation port). An isolated paper soak for `donch_d20_10` runs on a daily clock via `python -m stratforge.paper_family`. It is not SSOT and does not share the 4h journal.

## Components

- **live_node** — mode gates, lock, seed, health, 4h callback. Code: `stratforge/strategies/live_node.py`.
- **LiveDataFeed** — public REST 5m fold; only emits completed 4h buckets.
- **SignalEngine** — decision SSOT. Code: `stratforge/strategies/signal_engine.py`.
- **PaperExecutor / LiveExecutor** — sizing, fees, risk, journal. Code: `stratforge/strategies/paper_executor.py`, `stratforge/strategies/live_executor.py`.
- **research_replay** — same engine + CASH paper, no clock. Code: `stratforge/strategies/research_replay.py`.
- **viz** — projector in `stratforge/viz/projector.py`. It writes `viz/snapshots/` under the state directory. Snapshots and passwords are not in git.

## System diagram

```mermaid
flowchart TD
    Cron["refresh_recent_5m cron"] --> Parquet[("data/raw/*_USD_5m.parquet")]
    CB{{Coinbase US public 5m}} --> Feed[LiveDataFeed]
    Parquet --> Feed
    Feed --> Apply[apply_engine_bar]
    Apply --> Eng[SignalEngine]
    Eng -->|paper| PaperEx[PaperExecutor]
    Eng -->|live halted| LiveEx[LiveExecutor]
    PaperEx --> J1[("execution_log.jsonl")]
    LiveEx --> J3[("execution_log_live.jsonl")]
    LiveEx --> Ccxt{{ccxt_bridge}}
    Ccxt --> CBAT{{Coinbase Advanced Trade}}
```

Entry point: `python -m stratforge.live`  
Paper unit example: `deploy/stratforge-paper.service.example` → `config/live_paper.json`  
Donch paper soak (not SSOT): `python -m stratforge.paper_family` → `config/live_paper_donch.json`  
LIVE is off until `data/LIVE_APPROVED` exists in the state directory. See [PROMOTE.md](PROMOTE.md).

## Mode matrix

| Mode | Executor | Journal | Real orders | Gates |
|---|---|---|---|---|
| PAPER | `PaperExecutor` | `data/execution_log.jsonl` | No | None (SIMULATION default) |
| LIVE | `LiveExecutor` | `execution_log_live.jsonl` | Yes | `LIVE_TRADING=true`, `SIMULATION_MODE=false`, `data/LIVE_APPROVED`, `COINBASE_PORTFOLIO_ID`, keys |

Dry-run `LiveExecutor` remains in unit tests only. There is no node mode for it.

## Data flow

1. **Boot seed** — last ~15k 5m rows → 4h → `engine.on_bar(..., emit=False)` — `stratforge/strategies/live_data_feed.py`
2. **Journal restore + slot sync** — `stratforge/strategies/paper_executor.py` / `stratforge/strategies/live_executor.py`
3. **LIVE only: reconcile** — `stratforge/strategies/live_reconcile.py`. Boot, each 4h wave, **before every BUY**, and **before every CLOSE**. Healthy fetch is venue SSOT: extra USD → `CASH_DEPOSIT`, missing USD → `CASH_WITHDRAW`, lot drift → `BOOK_SYNC`. Wallet `available_balance` overlays rounded portfolio crypto lots. Failed fetch does not adopt. Clip = `equity × max_alloc_pct` (0.25). Dollar clip/book caps are off (`0`). Daily halt is **10%** of day-start equity. `min_notional_usd=5` only. **LIVE orders:** IOC limit at ask after spread/chase gates (`buy_protect`); no unbounded market buy. Close never sells above wallet available.
4. **Poll 5m, emit closed 4h** — `stratforge/strategies/live_data_feed.py`
5. **Apply** — `stratforge/strategies/bar_apply.py` → engine → executor
6. **Health** — `data/node_health.json` (+ `_live` twin when LIVE is on)

## SSOT

| Concern | Source |
|---|---|
| Entry / exit / rotation | `strategies/signal_engine.py` |
| Production knobs | `config/trend_rotation_25_max8_rotate.json` |
| What paper runs | `config/live_paper.json` (`_include` DNA) |
| Symbols | `config/universe_live.json` via `strategies/universe.py` |
| Paper / live book | `data/execution_log.jsonl` / `execution_log_live.jsonl` |
| Live MTM display | `data/node_health.json` → `equity` |
| Pre-trade risk | `risk_limits.py` + overlay `risk` block |
| Halt | `data/TRADING_HALTED` or `TRADING_HALTED_{paper,live}` |
| Order I/O | `ccxt_bridge.py` only (`native_v2` refused) |

## Key design decisions

- **One brain, two clocks.** Live uses REST 4h close; research uses parquet fold. Same `SignalEngine`.
- **Executors own money.** Engine emits `{type, symbol, reason}`; sizing/fees/halt live in the executor.
- **Rotation ≠ `max_concurrent`.** Need `rotate_when_full` + min-hold + underwater-only + `_long_slots`.
- **CASH only** for production research. Rejected: defensive max4 stack, hybrid LS as main DNA.
- **Package owns the code.** `ccxt_bridge` and `risk_limits` live in the `stratforge` package. A copy in the state directory shadows the install.
