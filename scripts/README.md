# scripts

| Script | Role |
|---|---|
| `paper_status.py` | Equity, positions, and recent trades |
| `refresh_recent_5m.py` | Incremental candle pull |
| `download_5m_top50.py` | Deep 5m pull |
| `staggered_backtest_status.py` | Path-dependence replay |
| `run_portfolio_backtest.py` | One-window CASH replay |
| `validate_dna.py` | Production DNA lock |

Run them with `STRATFORGE_STATE` set, from a venv that has the package installed.
