# Short book — `tsmom12_weekly`

The identity that ships is `config/short/tsmom12_weekly.json`.

- strategy_id: `tsmom12_weekly`
- Weekly 12-month time-series momentum, BTC and ETH, CFM perp-style, 1x
- Short a name only while its 12-month return is negative
- `atr_stop_mult`: 0
- Not mixed with the spot long book, its journal, or its portfolio

`bear_regime_cover` and the other `config/short/bear_regime_*.json` files are rejected variants. The scorecard below is that older research, not the shipping identity.

**Not** `trend_rotation_25`. Do not mix configs, engines, journals, or portfolios.

Approved scope (2026-08-13): US **CFM perpetual-style futures**, BTC + ETH only. Naive invert of the long DNA was skipped.

## Phase 0 — venue (assumptions labeled)

| Item | Value | Confidence |
|---|---|---|
| Products | nano BTC perp-style **BIP** (0.01 BTC), nano ETH **ETP** (0.10 ETH) | high (Coinbase Derivatives pages) |
| Who | Coinbase Financial Markets, CFTC | high |
| Hours | 24/7, weekly 1h Friday 17:00–18:00 ET | medium |
| Advertised fee | as low as 0.02%/contract | medium (Jul 2025 launch post) |
| Research fee | **4 bps/side** | conservative assumption |
| Funding | charged to shorts **0.3 bp / 8h**, never credited | conservative assumption; no CFM history in repo |
| Leverage in research | **1.0** | hard rule |
| Price series | Coinbase spot USD 5m → 4h | proxy; basis unmodeled |
| Out of scope | US spot short, offshore perps, alts, live orders | — |

Sources: Coinbase CFM launch post, Derivatives hours page, contract-spec help page. Live fee/margin must be read off the funded CFM account before any paper→live talk.

**Lot coarseness:** 1 BIP ≈ $1k notional at $100k BTC. Primary research capital **$10k** so 1× lots exist. $1k at 1× cannot hold 1 BIP.

## Baseline DNA (`config/short/bear_regime_cover.json`)

- Short BTC (50% equity notional) when **BTC 4h close < 200 EMA**
- Short ETH (50%) only if ETH is also below its own 200 EMA
- Cover all on BTC reclaim of 200 EMA
- Per-name **2× ATR** hard cover
- Cooldown 2 bars
- Unused sleeve stays cash
- `max_leverage=1`

## Scorecard (2026-08-13, BTC 2020–2026 filled)

Engine: `short/replay.py` + `ShortLedger` MARGIN_1X. Start $10k. Fee 4 bps. Funding 0.3 bp/8h.  
BTC 5m gap 2020-07-30→2023-07-18 **filled** (695,728 rows, years 2020–2026 complete).

| Window | BTC path | Strategy | Max DD | Trades | vs cash | vs always-short BTC |
|---|---|---:|---:|---:|---|---|
| **2021-11→2022-12 (72% bear)** | **−72%** | **+59.4%** | 19% | 64 | **win** | lag (+70%) |
| BTC-only 2022 | −72% | +48.8% | 10% | 22 | win | lag (50% sleeve) |
| 2023-07-18→2026-07-18 | +115% | **−1.2%** | 39% | 194 | ~flat | **beats** (−140%) |
| 2024-03→2024-09 (30% dip) | −6% | **−6.8%** | 21% | 35 | lose | lose (+4%) |
| 2025-01→2025-05 | +3% | **+1.2%** | 14% | 39 | small win | beats (−4%) |
| **2025-10→2026-07 (53% crash)** | **−43%** | **+40.0%** | 18% | 47 | **win** | tie (+41%) |
| Full 2020-07→2026-08 | +595% | **+11.9%** | 48% | 351 | win | **beats** (−711%) |

Integer nano lots ($10k): 2022 **+57.8%**, 2025-26 crash **+37.7%** — same story. 0 liquidations at 1×.

## Read of the edge

- **Makes money in real bears.** 2022 +59% (BTC −72%); 2025-26 crash +40% (BTC −43%).
- **Survives bulls.** Always-short BTC is −140% / −711% on mixed windows; this book is ~flat to +12%.
- **Does not beat 100% BTC short-and-hold through a crash** (50% cash sleeve + EMA chop). BTC-only 2022 at 50% (+49%) *does* beat half-size always-short (~+35%) because it sits cash on bear-market rallies.
- **2024 dip is the hole:** −6.8% vs dumb short +4%. 200-EMA 4h whipsaw (win rate ~24%).
- Next lever is slower/cleaner regime, **not** long-book defensive junk.

## Promote / kill

**Keep as research baseline** (not production, not paper).

1. ~~2022 BTC bear profitable~~ **PASS +59%**
2. Crash vs always-short: **tie** (2025-26) / **lag 11pp** (2022); mixed-window left tail **much better**
3. 2024-style chop on monthly births — **not tested**
4. Integer $10k lots — **PASS** (same sign/magnitude)

Do not paper until you promote. Chop fix is optional next research.

## Slower-regime gates (2026-08-13) — none promoted

Same 5 windows, $10k, 4 bps, funding 0.3 bp/8h. Runner: `short/run_regime_gates.py`. JSON: `results/backtests/short/regime_gate_compare.json`.

| Variant | 2022 | 2023-07→2026-07 | 2024 chop | 2025-10 crash | Full |
|---|---:|---:|---:|---:|---:|
| `bear_regime_cover` | **+59.4%** | −1.2% | −6.8% | **+40.0%** | **+11.9%** |
| `ema_falling` | +59.4% | −1.2% | −6.8% | +40.0% | +11.9% |
| `persist12` | **−25.8%** | +30.5% | +4.4% | +43.7% | −16.2% |
| `daily200` | +63.0% | **−25.4%** | −16.5% | +29.7% | −16.9% |

- **ema_falling** is tautological: for a standard EMA, `close < EMA` iff the EMA just ticked down. Identical trades. Dead end.
- **persist12** (12×4h + ATR-stop resets persist): fixes 2024 and the mixed window; **fails the 2022 must-pass** (8W/55L, bear-bar ret +2% vs baseline +134%). Same shape as the long-book defensive fail.
- **daily200** (entry only while daily close < daily 200 EMA): 2022 slightly better; every other window worse. 2024: 0W/7L.

Baseline stays `bear_regime_cover`. Do not stack these. Next short work needs a *different* idea, not a tighter 4h-EMA gate.

## Ground-up rethink (2026-08-13)

Objective (unchanged): isolated CFM 1× BTC/ETH book that **pays in real bears** and is **cash in secular bulls / 2024-style chop**. 4h-vs-200EMA cannot do that: it is in “bear” 47% of 2023-07→2026-07 with **218 flips**.

Occupancy of *other* bear definitions on our BTC 4h tape (`short/regime_occupancy.py` → `results/backtests/short/regime_occupancy.json`):

| Gate | 2022 frac/flips | 2023-26 | 2024 chop | 2025-26 crash |
|---|---|---|---|---|
| 4h 200 EMA (current) | 74% / 45 | 47% / **218** | 48% / 41 | 70% / 46 |
| daily 200 SMA | 87% / 19 | 39% / 55 | 20% / 7 | 90% / 9 |
| weekly 50 SMA | 86% / 25 | **23% / 17** | **0% / 0** | 86% / 11 |
| **12-month TSMOM < 0** | 77% / **5** | **22% / 9** | **0% / 0** | 82% / 9 |
| ATH −30% (cummax) | 88% / 23 | 36% / 42 | 0% | 64% / 33 |
| Donchian 55d state | 83% / 3 | 44% / 11 | **67% / 1** | 73% / 5 |

200-week SMA is a late-cycle *bottom* marker, not a short trigger (almost never on in-sample; 2022 not even initialized). ATH−30% on *all-time* high stays short through the 2023 bull (price still 30% below 2021 ATH) — unusable without a rolling peak.

**Stretches that matter:** 12m TSMOM is cash for all of 2023 through mid-Nov 2025, then on from ~$90k. 2022 on from 2022-02-10 @ $44k (misses the first leg). Weekly-50 similar, slightly earlier, more 1-bar flicker if evaluated on 4h.

That occupancy candidate was scored as `tsmom12_weekly` (below). Do not re-grid 4h-EMA.

## `tsmom12_weekly` — first score (2026-08-13)

`config/short/tsmom12_weekly.json`. Weekly close vs close[52w]. BTC 50% if 12m < 0; ETH 50% only if both 12m red. Cover on sign flip. **No ATR. No cat stop.** $10k, 1×, 4 bps, funding charged only. Runner: `short/run_tsmom12.py`. JSON: `results/backtests/short/tsmom12_weekly.json`.

| Window | tsmom12 | cover (old) | Max DD | Closes | Notes |
|---|---:|---:|---:|---:|---|
| 2022 | **+58.7%** | +59.4% | 17.5% | 0 (held) | BTC 2022-02-14 @ $41.9k, ETH 2022-05-02; still open YE |
| 2023-07→2026-07 | **+24.9%** | −1.2% | 17.9% | 2 | cash until 2025-11-24; bull-bar ret **0%** |
| 2024 chop | **0.0%** | −6.8% | 0% | 0 | never on |
| 2025-10 crash | **+23.7%** | +40.0% | 17.9% | 2 | late (Nov 24 @ $87k); still beats +15% kill |
| Full 2020-08→2026-08 | **+66.4%** | +11.9% | **30.6%** | 4 | 3W/1L; ~0.7 RT/year |

**Kill card: PASS** (2022>0, 2024≥−3%, crash≥+15%, RT/year≤4). Bull-bar equity change **0%** on every window except full (−0.1%). This is the idle-cash behavior 4h-EMA could not produce.

Research SSOT for the short book is now `tsmom12_weekly`. `bear_regime_cover` stays as the 4h-EMA baseline, not the lead. **Not paper. Not live.** Long `trend_rotation_25_max8_rotate` remains the wealth engine.

## Venue tenor (CFM)

US perpetual-style futures are **5-year dated**, not monthly. Current listing expires **20 Dec 2030**. Nano BIP = 0.01 BTC, nano ETP = 0.10 ETH. One contract covers every historical `tsmom12_weekly` hold (49–490 days). No quarterly roll. Live fee/margin still must be read off a funded CFM account before any paper→live talk.

## Notify-then-fund (ops design, 2026-08-14)

User decision: do **not** park idle margin in a short book through bulls. Run a watcher unfunded. When 12m flips red, notify; user wires capital (~1 day); bot opens the short. Bot covers on flip; user pulls cash back.

This matches the DNA. Decision clock is **Sunday 8:00 PM ET** (7:00 PM ET in winter; completed Sunday week). Holds are months. Occupancy ~34% of days over 2020-08→2026-08; **0% of 2024**; ~29 months cash Jun 2023→Nov 2025.

### Historical hold calendar (full window replay)

Events from `short/run_tsmom12.py` / `ShortLedger` on 2020-08-01→2026-08-13. $10k, 1×, 4 bps, funding charged only.

| Symbol | Open | Cover | Days | Entry → exit | PnL |
|---|---|---|---:|---|---:|
| BTC | 2022-02-14 | 2023-06-19 | **490** | 41931 → 26394 | +1853 |
| ETH | 2022-05-02 | 2023-05-29 | **392** | 2841 → 1904 | +1707 |
| ETH | 2025-11-24 | 2026-01-19 | **56** | 2855 → 3211 | −829 (only loser) |
| ETH | 2026-01-26 | 2026-03-16 | **49** | 2847 → 2236 | +1237 |
| BTC | 2025-11-24 | still open 2026-08-13 | **263** | 87408 → 63425 | +1827 uPnL |
| ETH | 2026-05-11 | still open 2026-08-13 | **95** | 2330 → 1885 | +1345 uPnL |

Range **7 weeks–16 months**. Six entries in six years (~0.7 RT/year). The 56-day ETH flicker is the awkward one — still long enough for a 1-day wire, too short to treat as set-and-forget for a year.

### Delayed-entry evidence (first-signal-wins)

Same full-cycle replay; SHORT queued at the Monday signal and filled at the first bar ≥ N days later. Cover still on-signal. Do **not** treat the 1–3d gain as an edge (n=6; Monday often bounced in-sample).

| Delay | Full ret | vs 0d | 2022 | Crash | Notes |
|---|---:|---:|---:|---:|---|
| 0d (on-signal) | +66.3% | — | +59.0% | +24.3% | SSOT fill |
| **1d** | **+71.4%** | **+5.1pp** | +60.2% | +26.9% | worst 1d slip +4.0% BTC 2022-02-14 |
| 3d | +77.9% | +11.6pp | — | — | sample noise / bounce |
| **7d** | **+37.6%** | **−29pp** | +54.5% | +9.5% | misses week-1 dump |
| 14d | +32.1% | −34pp | +49.6% | +13.1% | still all 6 filled |
| 30d | +3.2% | −63pp | — | — | edge gone |

Week-1 moves after the Monday signal (neg = already dumped before a late short): ETH 2022-05-02 **−13.5%**, ETH 2026-01-26 **−21.4%**, ETH 2026-05-11 **−9.1%**, BTC 2022-02-14 **−7.1%**. That is why 7 days eats the book and 1 day does not.

### Workflow (when we stand this up)

1. **Prerequisite:** CFM already onboarded. Empty short portfolio exists (separate from the live sleeve / long paper). Opening a new CFM account is **not** a 1-day task. The delay numbers assume a ready book.
2. **Watcher only** (next build, not built). No capital. Recompute 12m on each completed week (Sunday 8 PM ET). Telegram ping on flip to red *and* on flip back to cash.
3. User wires cash into the short book within **1–3 days**. Do not take a week.
4. Bot opens the short (BTC 50% if BTC 12m < 0; ETH 50% only if both 12m red). 1×. No ATR. No cat stop.
5. Capital stays until the cover ping. Do not yo-yo on 4h noise.
6. Bot covers on sign flip. User pulls cash back to the long book / bank.

**Not paper. Not live.** Do not mix engines, journals, systemd, or portfolios with `trend_rotation_25_max8_rotate`.

### Replay how-to

```bash
# run from the checkout
.venv/bin/python short/run_tsmom12.py
.venv/bin/python short/test_tsmom.py
```

Delay study was an ad-hoc replay on 2026-08-14 (first-signal-wins queue). Re-run from `short/tsmom.py` + `ShortLedger` if the calendar changes. Do not promote a delay-optimized variant.

## Do not

- Flip `long_only` on `signal_engine.py`
- Point long systemd / journals at this book
- Use 10× CFM leverage in research
- Call this `trend_rotation_25`
