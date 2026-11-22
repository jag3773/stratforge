# cfm

Isolated research for Coinbase Financial Markets nanos. Not the spot long book. Does not import `strategies`, `ccxt_bridge`, or `short`.

- Products: BIP, ETP, SLP, XPP
- Overnight leverage cap is 4x. Weekday intraday 10x is not used on 4h holds
- Integer lots. Funding is a debit, never a credit
- `USE_CFM_FUTURES` on the bridge is a flag, not an adapter

```bash
python -m stratforge.cfm.test_isolation
python -m stratforge.cfm.run_research
```
