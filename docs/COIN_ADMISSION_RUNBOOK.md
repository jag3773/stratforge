# Coin admission runbook — Option A (continuity expand)

[Architecture](COIN_ADMISSION_ARCHITECTURE.md) · [Production brief](PRODUCTION_BRIEF.md)

**Status:** MVP built 2026-08-06. Live bot reads **only** `config/universe_live.json`.  
**Never** point `live_node` at the watchlist.

## Commands (from the checkout, prefer `.venv`)

```bash
source .venv/bin/activate

# 0) Discover new Coinbase USD spot bases (writes data/admission/watchlist.jsonl)
python scripts/admission/discover.py --max-new 20
python scripts/admission/discover.py --dry-run

# 1) Data QA (online product + parquet/4h history gates)
python scripts/admission/data_qa.py                  # all discovered
python scripts/admission/data_qa.py --symbol ABC

# 2) Single-name SSOT CASH research (SignalEngine replay)
python scripts/admission/run_single_name.py --symbol ABC

# 3) Portfolio impact: admitted universe vs universe+1
python scripts/admission/run_universe_plus_one.py --symbol ABC

# 4) Path-dependence gate (required before non-force promote)
python scripts/admission/run_stagger_gate.py --symbol ABC

# 5) Optional shadow soak list (not live_node)
python scripts/admission/promote.py shadow-add --symbol ABC

# 6) Explicit promote into live universe JSON (does NOT restart paper)
python scripts/admission/promote.py promote --symbol ABC --yes
# escape hatch after human review only:
python scripts/admission/promote.py promote --symbol ABC --yes --force

# Demote
python scripts/admission/promote.py demote --symbol ABC --yes --reason demoted_ops

# Summary
python scripts/admission/scorecard.py
```

## After promote

1. Ensure `data/raw/<SYM>_USD_5m.parquet` exists (download/refresh scripts).  
2. `systemctl --user restart pro-trader-paper.service`  
3. Confirm journal restore (no wipe) + feed health includes new symbol.  
4. Watch 72h.

## Invariants

| Rule | Enforcement |
|---|---|
| Same strategy DNA | All research uses `config/trend_rotation_25_max8_rotate.json` via SignalEngine |
| CASH only | Replay PaperExecutor, no Nautilus MARGIN |
| Live ≠ watchlist | `strategies/universe.py` / `live_node` load `universe_live.json` only |
| Promote is explicit | `--yes` required; stagger_ok required unless `--force` |
| Caps | `max_admits_per_month` in `config/admission_thresholds.json` |

## Artifacts

| Path | Role |
|---|---|
| `config/universe_live.json` | Admitted SSOT |
| `config/universe_shadow.json` | Shadow soak list |
| `config/admission_thresholds.json` | Gates |
| `data/admission/watchlist.jsonl` | Latest status per symbol |
| `data/admission/scorecards/` | Evidence JSON |
| `data/admission/admission_log.jsonl` | Promote/demote audit |
| `data/admission/changelog.md` | Human log |

## Threshold note

**Calibrated 2026-08-12** (not placeholders). Evidence:
`results/admission/threshold_calibration_2026-08-12.json` (+ local
`data/admission/scorecards/` copies).

| Gate family | Method | Result on live n=50 |
|---|---|---|
| Single-name | cohort p25 trades/am/exp; p10 return; p90 DD | 31 research_ok (was 10) |
| + single-name stagger (cohort tool) | median≥0, worst_dd≤0.75 | **19 clear_bar (38%)** |
| universe+1 | DD worsen ≤0.10; Δret ≥ −0.20 | LOO-informed DD; operational ret floor |
| Promote stagger | same absolute stagger keys on **universe+1** portfolio | baseline portfolio births all pass |

Re-run after material universe or DNA change:
` .venv/bin/python scripts/admission/calibrate_cohort.py `
