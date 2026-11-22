# Coin Admission Architecture — Option A (Continuity Expand)

[Index](INDEX.md) · [Production brief](PRODUCTION_BRIEF.md)

**Status:** MVP implemented 2026-08-06 — see [COIN_ADMISSION_RUNBOOK.md](COIN_ADMISSION_RUNBOOK.md).  
**Product choice:** **Option A — Continuity expand** (same validated edge, more names that behave like the current cohort).  
**Not this doc:** Option B (listing/early-momentum sidecar with different risk). Keep B out of this funnel.  
**Canonical repo:** the checkout  
**Related:** [PRODUCTION_BRIEF.md](PRODUCTION_BRIEF.md), [GO_LIVE_RECIPE.md](GO_LIVE_RECIPE.md), [ARCHITECTURE.md](ARCHITECTURE.md)  
**Written:** 2026-08-05 · **MVP:** 2026-08-06

---

## Goal

Leave the **live production universe frozen** and proven. Run a **separate review track** that can **promote** Coinbase spot names into the live universe only after structured evidence (data QA → single-name backtest → portfolio impact → path-dependence → paper shadow → explicit admit).

**Early breakout** in Option A means: catch breakouts on additional names that pass the **same** strategy SSOT — not chase brand-new listings with a different product.

---

## Principles

1. **Two universes** — `LIVE_UNIVERSE` (ops-owned, frozen until admit) vs `CANDIDATE_POOL` / watchlist (research-owned).
2. **Promotion is a gate, not a feed** — scanners propose; backtests + checklist + human sign-off admit.
3. **Same strategy SSOT** — candidates tested only on production recipe:
   - Name: `trend_rotation_25_max8_rotate`
   - Decision SSOT: `strategies/signal_engine.py`
   - Config: `config/trend_rotation_25_max8_rotate.json` (+ live mirror `config/live_paper.json`)
   - Core knobs: 4h bars, breakout channel 60, slow EMA 200 exit, `max_alloc_pct=0.25`, `max_concurrent=8`, `rotate_when_full=true` (underwater-only), `rotate_min_hold_bars=14`, **CASH spot** (no MARGIN leverage fantasy)
4. **No look-ahead listing magic** — eligibility from **tradable-on-Coinbase** / first clean history (`eligible_from`), not “when we noticed it.”
5. **Path dependence required** — single full-sample backtest is insufficient; staggered / monthly-birth checks before promote (reuse stagger harness patterns).
6. **Live add is surgical** — one name (or small batch) at a time, changelog’d, reversible (demote path).
7. **Live bot never reads the watchlist** — only admitted universe sources.

---

## Current baseline (facts to respect)

- Fixed ~50 bases in `strategies/live_node.UNIVERSE` (same research/backtest cohort spirit).
- Feed: sequential REST with sleep budget → cycle time scales with symbol count; admits cost feed SLA.
- Production rejects known losers as main path (e.g. defensive max4 stack); do not dilute SSOT via silent universe junk.
- Spot backtests: **CASH only**. Equity = cash + open MTM.
- Paper restart restores `execution_log` (no wipe on strategy updates).

---

## Funnel / stages

```
Discovery → Data QA → Single-name research → Portfolio impact (universe+1)
    → Stagger / path-dependence → Paper shadow soak → Admission decision
    → Live promote (changelog) ↔ Demote
```

### Status model

`discovered → qa_fail | qa_ok → research_reject | research_ok → stagger_reject | stagger_ok → shadow → admitted | rejected | demoted`

Every terminal reject stores a **reason code** (see below).

---

### Stage 0 — Discovery (noisy, cheap)

**Purpose:** Propose candidates only.

**Sources (examples):**
- New Coinbase spot listings (USD/USDC)
- Liquidity / volume screens on Coinbase
- Simple breakout features for triage (range expansion, close > N-bar high) — **triage only**, not entry logic fork

**Output:** watchlist rows — **not** tradeable by live node.

**Caps / hygiene:**
- Max N new watches per day (TBD at build)
- Optional min time-on-exchange before QA (e.g. 7–30d) so pure listing pops are not the default Option A intake
- Dedupe by base symbol + venue

---

### Stage 1 — Data readiness (hard fail)

Before any strategy backtest:

| Check | Intent |
|---|---|
| Spot USD and/or USDC market online on Coinbase | Venue fit (US spot path) |
| Enough **4h** history for warmup | EMA200-safe; define `min_bars` + min calendar span |
| Gap / flat-print / absurd-spread filters | Clean series |
| Fee, lot size, min notional known | Executable sizing realism |
| `eligible_from` set | No pre-list fantasy fills |

**Fail → `qa_fail`.** Do not run strategy backtests on garbage data.

---

### Stage 2 — Single-name research backtest (cheap screen)

- Account: **CASH spot**
- Params: production SSOT only (no per-coin optimize in Option A)
- Window: `max(eligible_from, history_start)` → now (completed bars only)
- Metrics (minimum set): trade count, expectancy after fees, max DD, time-in-market, turnover / fee drag, activity span (months with trades)
- Compare to **baseline cohort distribution** (~current 50), not a fantasy benchmark

**Reject examples:** too few trades, catastrophic left tail, fee-eaten churn, single 30d spike only.

**Pass → `research_ok`.**

---

### Stage 3 — Cohort / portfolio impact (required)

New names compete for **8 slots** under rotate rules. Single-name edge can still **steal slots** from winners.

- Backtest **A:** `LIVE_UNIVERSE` (baseline)
- Backtest **B:** `LIVE_UNIVERSE ∪ {candidate}`
- Same max8 + rotate_when_full + rotate_min_hold_bars + CASH
- Ask: diversifying right tail vs noise vs worse left tail / slot theft

Optional experiments (research only): add-only vs replace-weakest — **default promote path is add-only** unless evidence says otherwise.

**Pass → ready for stagger.**

---

### Stage 4 — Path-dependence / stagger (promotion bar)

Reuse existing stagger discipline (`scripts/staggered_backtest_status.py` patterns, CASH equity, warmup + `trade_start_ns`):

- Monthly or N-week birth offsets on the **universe+1** (and/or single-name) cohort
- Report median/mean outcome, left tail (p5/p10 DD or equity), stability across births
- **Promote only if** not a one-path miracle

**Pass → `stagger_ok`.**

---

### Stage 5 — Paper shadow soak (ops)

- Add symbol to a **shadow universe** (paper path) — still **not** production `LIVE_UNIVERSE` until Stage 6–7
- Confirm feed: bars, no NaNs, health files sane; order sim / journal behavior sane
- Soak window: e.g. 2–4 weeks **or** N bars + N evaluated signals (tune at build; prefer time + health if low-trade regime)
- Compare shadow behavior to research expectations (regime sanity, not tick-perfect)

**Pass → eligible for admission decision.**

---

### Stage 6 — Admission decision (explicit)

Versioned checklist artifact (pass/fail + links to evidence):

- [ ] Data QA OK  
- [ ] Single-name research OK  
- [ ] Portfolio universe+1 OK  
- [ ] Stagger OK  
- [ ] Paper shadow soak OK  
- [ ] Risk/liquidity OK (ADV, spread, min notional vs 25% alloc at planned equity)  
- [ ] Feed budget OK (cycle time still acceptable)  
- [ ] Human approve (required for first N promotes; later optional semi-auto only with stricter thresholds)

Record: `admitted_at`, `config_version` / git ref, `evidence_paths`, `rollback_plan`.

---

### Stage 7 — Live promote (small, reversible)

1. Update admitted universe source (see File layout) + changelog entry  
2. Deploy/restart paper (or live-paper) per existing ops — **no execution_log wipe**  
3. Post-promote watch: 72h feed health, first signals, sizing sanity  
4. **Demote path:** remove name, status `demoted`, cooldown before re-review  

Live/real-money remains gated by existing go-live rules; this pipeline does **not** authorize LIVE trading.

---

## Reason codes (reject / demote)

| Code | Meaning |
|---|---|
| `thin_history` | Insufficient 4h bars / calendar span |
| `data_gaps` | Bad/missing candles, spreads, flats |
| `fee_churn` | Turnover kills expectancy after fees |
| `too_few_trades` | Not enough observations |
| `left_tail` | DD / p10 path unacceptable vs cohort |
| `one_regime` | Edge only in one short window |
| `slot_theft` | universe+1 hurts portfolio vs baseline |
| `stagger_fail` | Path dependence unstable |
| `liquidity` | Cannot support alloc at planned size |
| `feed_budget` | Symbol count breaks feed SLA |
| `shadow_fail` | Paper soak health/behavior fail |
| `policy` | Human/policy reject |
| `demoted_ops` | Removed post-admit for ops/risk |

---

## Thresholds (calibrated 2026-08-12)

SSOT file: `config/admission_thresholds.json`  
Evidence: `results/admission/threshold_calibration_2026-08-12.json`  
Refresh: `scripts/admission/calibrate_cohort.py`

**Single-name (continuity screen vs live cohort distribution):**

| Key | Value | Rule |
|---|---|---|
| `min_trades_single` | 3 | cohort p25 (DNA is sparse per name) |
| `min_active_months` | 2 | cohort p10/p25 |
| `min_expectancy_usd` | −35.87 | cohort **p25** (median single-name exp is negative; `>=0` rejected 52% of live names) |
| `min_net_return_pct_single` | −0.17 | cohort p10 |
| `max_drawdown_pct_single` | 0.35 | cohort p90 |

**Portfolio impact / stagger:**

| Key | Value | Rule |
|---|---|---|
| `universe_plus_one_max_p10_worsen_pct` | 0.10 | ~1.25× max DD impact in 12-name LOO sample |
| `universe_plus_one_min_median_delta_pct` | −0.20 | operational −20pp full-sample (LOO-remove of winners is asymmetric; not used as add floor) |
| `stagger_min_median_return_pct` | 0.0 | baseline portfolio births median ≫ 0; reject negative median path |
| `stagger_max_left_tail_dd_pct` | 0.75 | baseline worst_dd ≈ 0.69 + buffer |

**Cohort check after calibration:** 19/50 `clear_bar` (qa+single+single-name-stagger), 12 stagger_reject, 19 research_reject.  
Note: `calibrate_cohort` staggers **single-name**; promote path staggers **universe+1** (same absolute keys).

---

## System components (build targets)

| Component | Role |
|---|---|
| Watchlist store | Symbol, listing/eligible dates, source, status, notes, reason codes |
| `data_qa` job | History completeness, `eligible_from`, market online |
| `research_runner` | Single-name + universe+1 CASH backtests on SSOT config |
| `stagger_runner` | Path-dependence harness (wrap/reuse staggered scripts) |
| Scorecard | Pass/fail vs thresholds + cohort percentiles; artifact paths |
| Shadow universe | Paper soak feed list ≠ live universe until promote |
| Admission log + changelog | **Only** write path into live universe |
| Cron + Telegram digest | Discovery/QA cadence; weekly scorecard; no huge code fences in TG |

**Invariant:** `strategies/live_node.py` (or successor universe loader) consumes **admitted** list only.

---

## Suggested file layout (when building)

```

  docs/COIN_ADMISSION_ARCHITECTURE.md    # this file (SSOT for design)
  config/
    trend_rotation_25_max8_rotate.json   # strategy SSOT (unchanged by discovery)
    live_paper.json                      # live knobs mirror
    universe_live.json                   # OPTIONAL future: admitted bases (replace hardcoded list)
  data/admission/                        # runtime artifacts (gitignore heavy outputs if needed)
    watchlist.jsonl                      # or sqlite — choose at MVP
    scorecards/<symbol>_<ts>.json
    admission_log.jsonl
    changelog.md                         # human-readable promote/demote
  scripts/admission/                     # new package at build time
    discover.py
    data_qa.py
    run_single_name.py
    run_universe_plus_one.py
    run_stagger_gate.py
    scorecard.py
    promote.py                           # checklist + write universe + log (no silent edit)
    demote.py
```

**MVP universe wiring:** keep `live_node.UNIVERSE` as source of truth initially; `promote.py` may patch a single shared universe module/JSON **only** via explicit checklist. Longer-term: load `config/universe_live.json` from one place (live_node + research scripts) to avoid drift.

**Research scripts today with local UNIVERSE copies** (`portfolio_run.py`, stagger status, etc.) must be reconciled at build so admission tests the **same** live list.

---

## MVP → later

### MVP (first useful slice)

1. Watchlist store (JSONL is enough)  
2. Data QA  
3. Single-name SSOT backtest (CASH)  
4. Universe+1 backtest vs baseline  
5. Manual checklist + admission_log + changelog  
6. Manual universe edit via promote helper (no auto-trade)

### Next

- Stagger gate required for promote  
- Telegram scorecard digest  
- Thresholds calibrated to cohort percentiles  

### Later

- Shadow universe auto-soak  
- Demote rules / cooldown  
- Optional semi-auto promote with strict caps (still changelog + rollback)  
- Category caps + feed-budget simulator  

---

## Explicit non-goals (Option A)

- Auto-trade every new Coinbase listing  
- Expand toward all ~400 Coinbase bases without gates  
- Per-coin parameter optimization inside this funnel  
- Option B listing-momentum product mixed into the same admit criteria  
- Treating one juicy window as proof without stagger  
- Live bot polling watchlist or discovery output  
- Any real-money enablement (still `GO_LIVE_RECIPE` / user approval)

---

## Safety

- Paper / research only until user explicitly approves promote into the running paper node universe  
- LIVE trading unchanged: dual flags, approval file, risk_limits — admission ≠ go-live  
- US/CFTC-conscious: Coinbase **spot** long-only path  
- Prefer stop-and-report over spinning on ambiguous candidate research  

---

## Build pickup checklist

When implementing, do in order:

1. Freeze design choices: store format (JSONL vs sqlite), universe single-source file, soak defaults, K admits/month  
2. Calibrate thresholds on current ~50 cohort distributions (export baseline scorecards once)  
3. Implement MVP path end-to-end on **one** deliberate candidate (dry promote, no need to keep it)  
4. Wire stagger gate before any real promote into the running paper universe  
5. Document ops runbook section in this file or a short `docs/COIN_ADMISSION_RUNBOOK.md`  
6. User checkpoint before first production-paper universe change  

---

## Decision log

| Date | Decision |
|---|---|
| 2026-08-05 | Adopt **Option A (continuity expand)** as the admission product; sketch saved as this architecture doc. Option B deferred / separate sidecar if ever pursued. |
| 2026-08-05 | Live universe remains fixed until gated promote; discovery must not feed live_node directly. |
| 2026-08-06 | MVP shipped: `config/universe_live.json` SSOT, `scripts/admission/*`, SignalEngine CASH replay research, promote/demote with checklist. |
| 2026-08-12 | Thresholds calibrated on live n=50 cohort percentiles + baseline portfolio stagger + LOO DD sample. Placeholders retired. |
