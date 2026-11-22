# CFM isolated research

Long/venue research for Coinbase Financial Markets (CDE perp-style nanos).
**Not** `trend_rotation_25_max8_rotate`. **Not** the live sleeve. **Not** paper/live.
Does not import `strategies.*`, `ccxt_bridge`, or `short.*`.

## Run

```
# run from the checkout
.venv/bin/python cfm/test_isolation.py
.venv/bin/python cfm/test_ledger.py
.venv/bin/python cfm/run_research.py
.venv/bin/python cfm/run_round2.py
```

Yardstick: `cfm_btc_1x_hold`. Overnight leverage cap **4x** (10x is weekday intraday only).
Integer nanos (BIP 0.01 BTC). Funding: constant debit, never credited.
Price proxy: spot 5m→4h. Basis unmodeled.

Start book research default **$1000** (small enough for 1 BIP at overnight IM).
