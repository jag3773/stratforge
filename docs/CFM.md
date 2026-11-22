# CFM long research (`cfm/`)

Isolated sibling of `short/`. Research only. No paper node, no the live sleeve, no live orders.

Spot DNA (`trend_rotation_25_max8_rotate`) is **not** portable onto CDE: different universe, integer lots, funding, overnight IM, liquidation.

`USE_CFM_FUTURES` on `ccxt_bridge` is a status flag, not an adapter.

## Venue (labeled)

- Product: US perp-style dated nanos (BIP/ETP/SLP/XPP), cash-settled USD, CFTC/CFM
- Overnight research cap: **4x** (IM ~25%). Advertised **10x is weekday intraday** (6pm–4pm ET) and is not used on 4h holds
- Fees: 4 bps/side research default (conservative vs ~$0.10/BIP)
- Funding: 0.3 bp / 8h, **never credited**
- Lots: BIP 0.01 BTC, ETP 0.10 ETH, SLP 5 SOL, XRP 500
- Small book: **$1000** (1 BIP notional ~$260 at 2023-08, overnight IM ~$65)

## CDE tape (pulled 2026-08-27)

Public Advanced Trade candles. **No 3-year CDE history** — BIP/ETP list **2025-07-18**, SLP/XPP **2025-08-15**.

| file | first 1h | last | rows |
|---|---|---|---:|
| `data/cfm/BTC_CDE_1h.parquet` | 2025-07-18 22:00 UTC | 2026-08-27 12:00 | 9614 |
| `data/cfm/ETH_CDE_1h.parquet` | 2025-07-18 22:00 UTC | 2026-08-27 12:00 | 9627 |
| `data/cfm/SOL_CDE_1h.parquet` | 2025-08-15 22:00 UTC | 2026-08-27 12:00 | 8962 |
| `data/cfm/XRP_CDE_1h.parquet` | 2025-08-15 22:00 UTC | 2026-08-27 12:00 | 8961 |

`.venv/bin/python cfm/download_cde.py` — parquet gitignored under `data/`.

Live product snapshot: BIP funding **0.0012% / hour**, overnight IM long **24.56%** / short **30.64%** (~4.1× / 3.3×), intraday IM ~10%. **No public funding-rate history.** 5m CDE candles 400; 15m/1h/4h work.

BIP vs BTC spot on 9608 overlapping hours: mean basis **+2.0 bp**, p95 |basis| ~8 bp, corr 0.999998. Spot proxy is tight **after listing**; it cannot invent pre-Jul-2025 CDE prices.

## CDE-tape replay (2025-07-19 → 2026-08-27)

BIP **118425 → 79515 = 0.671×** (−33%). Hourly funding 0.0012% debit, 4 bps, integer BIP, overnight 4×. Runner: `cfm/run_cde_replay.py`.

$1000 cannot open **1×** (1 BIP notional ~$1.2k at listing). True 1× at $2500 ended **0.61×** (funding drag vs 0.67× price). Always **3× long liquidated** (0.21×).

| identity | book | vs BIP | DD | trades | note |
|---|---:|---:|---:|---:|---|
| `cfm_turtle_330_120_r0.02_atr2_long_btc` | 1.11× | 1.65× | 2.2% | 2 | mostly flat; 1 entry |
| `cfm_wsma40_long_lev3_holdc_btc` | 1.07× | 1.60× | 6.8% | 2 | 40w not ready until ~Apr 2026 |
| `cfm_wsma40_ls_lev3_holdc_btc` | 1.07× | 1.59× | 40% | 4 | shorts extra DD, not extra money |
| `cfm_wsma10_long_lev3_holdc_btc` | 0.59× | 0.89× | 48% | 12 | too fast |
| `cfm_bip_hold_lev3` | 0.21× | 0.31× | 81% | 2 | 1 liq |

40-week SMA **cannot be validated on CDE tape** — 40 weeks of warmup leaves ~4 months. It beat BIP by being **out** of the listing-to-crash dump. That is not a 3y 10× result and not a reason to retune n.

## 3y bar (2023-08-28 → 2026-08-27 ET window on UTC tape)

BTC buy-and-hold: **3.03x** (+203%). Max DD **−54%** (2026-07-01).
Strict win = strategy multiple ≥ **30.3x** (10× BTC multiple) after fees, funding, integer lots, intra-bar liq.

**No identity in the literature battery cleared 30x.** Best 3y vs BTC:

| strategy_id | multiple | vs BTC | max DD | 2022 | liq |
|---|---:|---:|---:|---:|---:|
| `cfm_wsma40_ls_lev3_holdc_btc` | 12.12x | 4.00x | 59% | 1.98x | 0 |
| `cfm_wsma40_long_lev3_holdc_btc` | 11.34x | 3.74x | 47% | 0.75x | 0 |
| `cfm_faber10m_long_lev3_holdc_btc` | 7.00x | 2.31x | 42% | 1.03x | 0 |
| `cfm_ema300_1200_long_lev2_btc` | 5.17x | 1.71x | 58% | 0.85x | 0 |
| `cfm_tsmom365d_long_lev2_btc` | 5.15x | 1.70x | 59% | 0.83x | 0 |
| `cfm_turtle_330_120_r0.02_atr2_ls_btc_eth_sol` | 3.57x | 1.18x | 36% | 1.10x | 0 |
| `cfm_btc_1x_hold` | 2.36x | 0.78x | 54% | 0.39x | 0 |

3–4x **constant-leverage** weekly rebalance liquidates on path. Hold-contracts + ≤3x is the survivable shape.

Path-dependent note (not the requested 3y): `cfm_turtle_330_120_r0.05` on BTC/ETH/SOL printed 50.7x (22× BTC) on 2020-08→2023-08 and 112x on 2021-01→2026-08. That is a **size knob + window** that includes the 2021 alt season — not a validated 3y 10×-BTC result. Literature turtle risk is 2%.

## Why 10× BTC on this 3y is a bound, not a tuning miss

Overnight 4x is the honest CFM hold cap. 4x weekly re-leverage liquidates through BTC’s 4h ranges. Survivable ~2–3x with hold-contracts cannot turn a 3.03x BTC path with a −54% interior DD into a 30x book without a near-peak exit that 12m TSMOM / 200d EMA did not give.

## Isolation

`cfm/` must not import `strategies.*`, `ccxt_bridge`, `short.*`. Do not edit the spot host to “add CFM.”

## Paper gate (2026-08-27)

Frozen identities (ISO-week SMA, not 4h-index weeks):

| | long-only | long/short |
|---|---|---|
| config | `config/cfm/wsma40_long_lev3_holdc.json` | `config/cfm/wsma40_ls_lev3_holdc.json` |
| knobs | sma_weeks=40, lev=3, hold_contracts, BTC, overnight cap 4x | same + shorts |
| primary 3y | **11.40×** / 3.73× BTC / DD 47.5% / liq 0 / 7 flips | 12.31× / 4.02× BTC / DD 60.6% / liq 0 |
| 2022 | 0.97× (BTC ~0.35), DD 19%, liq 0 | 2.66×, DD 53%, liq 0 |
| 2025 | 0.59×, DD 66%, liq 0 | 0.59×, DD 71%, liq 0 |
| 36× 3y rolling | beat BTC **35/36**, med 3.33× BTC, med 9.0× book, liq in **3/36** | beat 29/36, med 3.00× BTC, liq in **16/36** |
| worst 3y birth | 2021-03 0.97×, DD 92.5% | 2021-05 0.47×, DD 93% |
| fee 8 bps | still 3.71× BTC | still 3.99× BTC |
| funding ×5 | 1.99× BTC | 1.72× BTC |
| $500 integer | 10.65× | 10.72× |
| 1-week lag | 2.44× BTC | 1.54× BTC |

Lookback robustness on the **same 36 births** (long-only lev 3 holdc):

| n weeks | med vs BTC | beat BTC | liq births |
|---|---:|---:|---:|
| 30 | 0.91 | 13/36 | 3 |
| **40** | **3.33** | **35/36** | 3 |
| 50 | 1.60 | 30/36 | 3 |

40 is best on this tape (10-month/40-week is a known CTA length, not a random grid). 30 fails. 50 still beats BTC. Do **not** retune n.

### Decision

- **Paper candidate: long-only only.** `cfm_wsma40_long_lev3_holdc_btc`
- **Do not paper the L/S book.** 16/36 births liquidate. Extra 3y vs-BTC is path luck, not a safer sleeve.
- **Not live. Not the live sleeve. No CFM API yet.** Paper means a new `cfm/` journal using the spot BTC 4h proxy — separate halt file, separate $1000, never `pro-trader-paper.service`.
- Overlapping 3y births are sensitivity, not 36 independent proofs.
- Same-end last 18 months (start 2025-02): 0.63× book / 0.81× BTC. A book opened into the 2025–26 dump is still in a hole vs its own start.

Runner: `.venv/bin/python cfm/run_validate.py` → `results/backtests/cfm/validate/stagger_stress.json`

