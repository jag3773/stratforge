# Promote a strategy

Paper and live must import the same decision code. Promotion is a config and a gate, not a second engine.

## Paper

1. Pick a strategy config. Production spot DNA is `config/trend_rotation_25_max8_rotate.json`. Donchian paper uses `config/donch_d20_10.json` on its own journal. Short and CFM do not share that journal.
2. Point the paper config at that file with `_include`.
3. Run `strategies/live_node.py` or `strategies/paper_family_node.py` with `LIVE_TRADING=false`.
4. The journal under `data/` is the book. It is not part of this repo.

## Live

All of these are required. None of them can be skipped with an environment flag:

- `LIVE_TRADING=true`
- `SIMULATION_MODE=false`
- `data/LIVE_APPROVED` present in the process working directory
- `COINBASE_API_KEY` and `COINBASE_API_SECRET`
- `COINBASE_PORTFOLIO_ID` set to the sleeve you mean. There is no default.

Put secrets in `.env.live` (mode 600) and pass that file to the process. Do not commit it. A failed venue read must not be treated as an empty book.

Generic unit: `deploy/stratforge-live.service.example`. Fill in the state directory. Do not put a key in the unit file.
