# Data and persistence

[Wiki](README.md) · [Architecture](ARCHITECTURE.md)

What we store, who writes it. Redis/Postgres are **not** installed and are not needed.

## 5m warehouse

| | |
|---|---|
| **Where** | `data/raw/<SYM>_USD_5m.parquet` |
| **Writer** | Hermes cron `refresh_recent_5m` (every 20m), **not** the node |
| **Reader** | `LiveDataFeed.seed()` at boot (last ~15 000 5m rows → 4h, ~50 days) |
| **Deep pull** | `scripts/download_5m_top50.py` |

The node **never writes** parquet. After boot it streams 5m in RAM via public REST.

If the box is down, both the node and the refresher are down. Gap-fill on restart only pulls ~50 recent 5m candles/symbol. After a multi-hour outage, refresh history before trusting the next signals.

## Journals (book SSOT)

| File | Role |
|---|---|
| `data/execution_log.jsonl` | Paper (`OPENING_BALANCE`, `CASH_DEPOSIT`, `PAPER_BUY/CLOSE`) |
| `data/execution_log_donch.jsonl` | Isolated `donch_d20_10` paper soak (do not mix with SSOT paper) |
| `data/execution_log_live.jsonl` | LIVE sleeve (do not wipe) |
| `data/archive/shadow_node_retired_2026-08-22/` | Retired dry-run journal + health (not a node; do not restore) |

Restart replays the journal. **Do not wipe** on strategy deploys. `paper_status.py` prefers `node_health` equity; journal is cash/lots/24h (equity fallback if node down).

## Health / ops

| File | Role |
|---|---|
| `data/feed_health.json` | Feed cycle / last success (`_live` twin when LIVE authorized; `_donch` for daily soak) |
| `data/node_health.json` | bars_seen, equity (`_live` / `_donch` twins) |
| `data/live_paper.log` | systemd stdout (`live_paper_donch.log` for soak) |
| `data/trade_alert_cursor.json` | Alert poller offsets |
| `data/sre_daily_latest.md` | Latest SRE verdict (git-tracked) |
| `data/sre_daily_reports/YYYY-MM-DD.md` | Dated SRE (git-tracked) |

Rest of `data/` is gitignored.

## Reboot recovery

Boot does **not** replay 30 days of live 5m. It re-seeds the parquet tail.

| Step | Time |
|---|---|
| Journal restore | instant |
| Seed + warm indicators | ~3 s |
| REST poll loop | immediate |
| Next BUY/CLOSE | next 4h close (0–4 h) |

Open lots are back immediately. New decisions wait for the 4h clock.
