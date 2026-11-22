# config/ — active configs only

| File | Role |
|---|---|
| [`trend_rotation_25_max8_rotate.json`](trend_rotation_25_max8_rotate.json) | **Production strategy DNA** |
| [`live_paper.json`](live_paper.json) | **systemd paper node** (`_include` production DNA) |
| [`live_production.json`](live_production.json) | Full-size LIVE config (same include; still gated) |
| [`live_micro.json`](live_micro.json) | Micro-capital LIVE sleeve (stage 1) |
| [`universe_live.json`](universe_live.json) | Admitted live/paper universe SSOT |
| [`admission_thresholds.json`](admission_thresholds.json) | Option A promote gates |
| [`trend_rotation_25pct.json`](trend_rotation_25pct.json) | Research baseline max50, no rotate |
| [`trend_rotation_25_baseline_max50.json`](trend_rotation_25_baseline_max50.json) | Named baseline twin |
| [`short/tsmom12_weekly.json`](short/tsmom12_weekly.json) | Isolated short research SSOT |
| [`short/bear_regime_cover.json`](short/bear_regime_cover.json) | 4h-EMA short baseline (not lead) |

Rejected / legacy JSON: [`../archive/config/`](../archive/config/).  
DNA lock: `scripts/validate_dna.py` (local `scripts/smoke_check.sh` is the host soak gate; GitHub `ci-lite` runs DNA + `pytest tests/`).

Docs: [../docs/PRODUCTION_BRIEF.md](../docs/PRODUCTION_BRIEF.md) · [../README.md](../README.md)
