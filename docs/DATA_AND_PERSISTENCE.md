# Data

Files below live in the state directory (`STRATFORGE_STATE`), not in the package.

## Candles

`data/raw/<SYM>_USD_5m.parquet`. The node reads the tail at boot and then polls public REST. It does not write parquet.

## Journals

| File | Role |
|---|---|
| `data/execution_log.jsonl` | Paper book |
| `data/execution_log_donch.jsonl` | Donchian paper. Do not mix with the paper journal |
| `data/execution_log_live.jsonl` | Live book |

A restart replays the journal. Do not delete it to deploy code.

## Health and gates

| File | Role |
|---|---|
| `data/node_health.json` | Equity and bars (`_live` and `_donch` twins) |
| `data/feed_health.json` | Last successful poll |
| `data/LIVE_APPROVED` | Required before live orders |
| `data/TRADING_HALTED` | Blocks new buys. Also `TRADING_HALTED_paper` and `TRADING_HALTED_live` |

Open lots are restored immediately. The next decision waits for the next closed 4h bar.
