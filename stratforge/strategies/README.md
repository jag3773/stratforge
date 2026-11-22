# strategies

| File | Role |
|---|---|
| `signal_engine.py` | Decision code. `SignalConfig.from_dict` reads the JSON knobs |
| `config_load.py` | Resolves JSON `_include` |
| `research_replay.py` | Same engine, CASH paper, no clock |
| `live_node.py` | Paper and live loop |
| `live_data_feed.py` | Public REST 5m folded to 4h |
| `paper_executor.py` | Paper fills and journal restore |
| `live_executor.py` | Live orders through `ccxt_bridge` |
| `live_reconcile.py` | Live book versus the venue. A failed fetch does not adopt |
| `universe.py` | Admitted names from `config/universe_live.json` |
| `bar_apply.py` | One closed bar through the engine and the executor |

Entry point is `python -m stratforge.live`, not this directory as a script path.

Docs: [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)
