# scripts/

| Script | Role |
|---|---|
| `paper_status.py` | Equity / positions / 24h trades (paper + LIVE sleeve) |
| `feed_health_check.py` | Stall watchdog (`QUIET=1` silent OK); live pair when authorized |
| `trade_alert_poll.py` | Journal tail alerts (empty stdout = silent) |
| `refresh_recent_5m.py` | Incremental parquet top-up (repo-relative paths) |
| `download_5m_top50.py` | Deep/historical 5m pull |
| `staggered_backtest_status.py` | Path-dependence CASH harness |
| `scaleup_birth_sensor.py` | Newest births vs early-m10 shape (add-size GO/NO-GO; read-only) |
| `run_max8_rotate_monthly.py` / `run_max8_stress.py` | Cohort runners |
| `live_preflight.py` | Paper/LIVE blocker list (never enables LIVE) |
| `ops_watchdog.py` | Dup/missing LIVE PID / halt / feed stall |
| `daily_sre_snapshot.py` | Read-only host/live/paper facts for the 06:00 SRE agent |
| `daily_sre_deliver.py` | Telegram ping: one line if GREEN, full report otherwise |
| `backup_journals.py` | Local journal copies |
| `validate_dna.py` | Production DNA JSON lock (CI + smoke) |
| `smoke_check.sh` | Local full unit + DNA smoke (`.venv`) |
| `admission/*` | Coin admission funnel |
| `universe_policy/*` | Ocean bake-off research |
| `test_paper_executor.py` / `test_signal_parity.py` | Must-pass regressions |
| `test_phase2_execution.py` | LiveExecutor dry-run + LIVE refuse |
| `test_p0_live_safety.py` / `test_live_reconcile_parse.py` / `test_p1_ops.py` / `test_chaos_journal.py` / `test_sre_live_ops.py` | QA/SRE safety |
| `test_risk_limits.py` / `test_admission.py` / `test_feed_emit_gate.py` | Limits / admit / 4h emit-before-close |
| `download_coinbase_1m.py` | Resumable 1m warehouse pull (`data/raw/*_USD_1m.parquet`; not used by paper/research) |
| `download_ccxt_data.py` | Legacy 3-pair 1m pull (superseded by `download_coinbase_1m.py`) |

Hermes installs thin wrappers from [`../deploy/hermes-cron-wrappers/`](../deploy/hermes-cron-wrappers/).  
Setup: [../docs/FRESH_VPS_SETUP.md](../docs/FRESH_VPS_SETUP.md)
