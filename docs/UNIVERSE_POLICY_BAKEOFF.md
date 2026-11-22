# Universe policy bake-off

[Index](INDEX.md) · [Coin admission](COIN_ADMISSION_ARCHITECTURE.md) · [Go-live](GO_LIVE_RECIPE.md)

**Status:** Research complete through **k50_expanded** (2026-08-13).  
**Question:** Is there a *rule* for choosing a Coinbase subset for `trend_rotation_25_max8_rotate` that beats the legacy live-50 **and** random eligible names under path-dependent (monthly birth) evaluation?

**Headline answer:** **No** — on expanded ocean (144 local USD 5m names, K=50, 37 births), **legacy50 still wins** mean/median/left-tail/beats-BTC. fit/liquidity/random all lose pairwise. **Freeze live universe.**

## Design

Universe construction is tested like strategy research:

1. **Eligibility E_t** — at birth date t, using only data **strictly before** t  
   - local `data/raw/<SYM>_USD_5m.parquet` ocean (expandable)  
   - min ~250 4h bars, min 120 calendar days  
   - min trailing median daily quote volume (default $50k)  
2. **Selection S(E_t) → U_t** — policy picks K names (or legacy intersection)  
3. **Evaluate** — frozen DNA CASH max8+rotate, same stagger windows as production cohort  
4. **Scorecard** — median/mean/min/max return, left tail, beats BTC, open_max=8, pairwise vs legacy50  

### Policies

| Name | Rule |
|---|---|
| `legacy50` | `config/universe_live.json` ∩ E_t |
| `liquidity_K` | Top K by trailing 30d median daily quote vol |
| `random_K_s*` | K uniform samples from E_t (multiple seeds) → `random_pool` |
| `fit_trendcap_K` | Rank frac days above 50d SMA + liquidity |
| `fit_strategy_K` | Top K by trailing 180d **single-name SSOT** strategy return |

DNA always: `config/trend_rotation_25_max8_rotate.json`  
(`max_alloc=0.25`, `max_concurrent=8`, `rotate_when_full`, `hold=14`, CASH, 40 bps)

## Commands

```bash
source .venv/bin/activate

# 1) Build daily feature panel from local parquets
python scripts/universe_policy/eligibility.py
# or via bakeoff:
python scripts/universe_policy/run_bakeoff.py --build-panel --skip-fit-strategy --births 2026-07-02 --k 30

# 2) Optional: single-name strategy curves for fit_strategy (once, cached)
python scripts/universe_policy/strategy_fit.py

# 3) Full bake-off (37 monthly births → 2026-08-02)
python scripts/universe_policy/run_bakeoff.py \
  --build-panel --build-fit-curves \
  --k 30 --random-seeds 5 \
  --first-birth 2023-07-02 --end 2026-08-02

# Smoke subset
python scripts/universe_policy/run_bakeoff.py --k 30 --random-seeds 2 \
  --births 2025-10-02,2026-07-02 --label smoke
```

## Outputs

| Path | Role |
|---|---|
| `data/universe_policy/features_panel.parquet` | Daily ocean features |
| `data/universe_policy/strategy_fit_curves/` | Per-name equity curves |
| `data/universe_policy/bakeoff/<label>/` | Per-policy birth JSONs + selections |
| `data/universe_policy/bakeoff/<label>/scorecard.json` | Full scorecard |
| `results/backtests/universe_policy_bakeoff/scorecard_latest.json` | Tracked compact copy |

## Win / kill criteria

**Keep a policy** only if vs `legacy50` **and** vs `random_pool` it improves the birth distribution on:

- left tail (min / p10 / stress births e.g. 2025-10), and/or  
- median return without destroying left tail  
- `engine_open_max <= 8`

**Kill / freeze legacy** if no policy beats random_pool on left tail + pairwise births.

## Results

### Phase-1 `k30_full` (2026-08-12) — local ~50 ocean, K=30
Method validation. legacy50 held; random_pool ≈ legacy mean but worse beats-BTC; fit/liquidity pairwise worse.  
Scorecard: `results/backtests/universe_policy_bakeoff/scorecard_k30_full.json`

### Phase-2 `k50_expanded` (2026-08-13) — true K=50 choice
| | |
|---|---|
| Ocean | 144 local `*_USD_5m.parquet` (candidates 150) |
| E_t late | \|E_t\| ≈ 115–127 |
| K | 50; liq floor median daily quote vol ≥ $25k |
| DNA | max_alloc=0.25, max8, rotate, hold=14, CASH, 40 bps |
| Births | 37 monthly 2023-07-02 → end 2026-08-02 |

| Policy | Avg | Med | Min | p10 | Beats BTC | vs legacy imp/worse |
|---|---:|---:|---:|---:|---:|---|
| **legacy50** | **+151.5** | **+67.1** | **−69.5** | **−28.0** | **33/37** | — |
| random_pool | +75.9 | +0.8 | −89.5 | −77.2 | 21/37 | 9 / 28 |
| fit_trendcap_50 | +82.2 | −1.3 | −91.3 | −75.4 | 21/37 | 8 / 29 |
| fit_strategy_50 | +71.9 | −21.8 | −87.1 | −78.9 | 19/37 | 8 / 29 |
| liquidity_50 | +69.2 | −8.6 | −91.1 | −72.5 | 18/37 | 3 / 34 |

All policies `engine_open_max=8`. Scorecard: `results/backtests/universe_policy_bakeoff/scorecard_k50_expanded.json`.

### Phase-3 `full_ocean_eligible` (2026-08-13) — **no K cap**
Every downloaded name that passes E_t at birth t (avg ~76 symbols; late ~127). Same DNA / births / liq floor $25k.

| Policy | Avg | Med | Min | p10 | Beats BTC | vs legacy |
|---|---:|---:|---:|---:|---:|---|
| **legacy50** | **+151.5** | **+67.1** | **−69.5** | **−28.0** | **33/37** | — |
| all_eligible | +85.4 | +0.8 | −89.5 | −78.0 | 22/37 | **7↑ / 30↓** (avg −66 pp) |

Scorecard: `scorecard_full_ocean_eligible.json`. **Opening the full downloaded pool under max8 hurts reliability** vs legacy — lottery max (+896%) with much worse median/left tail.

**Kill:** no tested S beats legacy50 and random_pool on left tail + pairwise. Expanding the ocean / dropping K made non-legacy policies **worse**, not better.

### Re-run expanded path
```bash
.venv/bin/python scripts/universe_policy/run_expanded_bakeoff.py
# full ocean (no top-K):
.venv/bin/python scripts/universe_policy/run_bakeoff.py \
  --skip-fit-strategy --min-quote-vol 25000 \
  --policies legacy50,all_eligible --label full_ocean_eligible \
  --first-birth 2023-07-02 --end 2026-08-02
# or K=50:
.venv/bin/python scripts/universe_policy/run_bakeoff.py \
  --build-panel --build-fit-curves \
  --k 50 --random-seeds 5 --label k50_expanded \
  --first-birth 2023-07-02 --end 2026-08-02
```

## Ocean note

Local ocean is now **~144** USD 5m parquets (not the old ~50). Further Coinbase names can still be added via `crypto-data-download` / ocean list builders; bake-off resumes by label.

## Explicit non-goals

- Full-sample top-N by return (Clear19 trap)  
- Optimizing DNA in the same run as universe policy  
- Auto-promoting bake-off winners into `universe_live.json` without user checkpoint  
