# AGENTS.md

Rules for coding agents in `stratforge`. Read before the first edit.

## 1. Where code goes

- Python `>=3.12`. Use the checkout `.venv` (`uv`). Do not add another installer.
- Package code is `stratforge/`. Spot decision and order code: `strategies/`, `ccxt_bridge.py`, `risk_limits.py`. `short/` and `cfm/` are separate packages. Do not import across those three.
- Order and feed I/O stays synchronous. Order I/O stays in `ccxt_bridge.py`. Do not add a second client.
- New production behavior is a new module. Do not append it to `live_executor.py` or `strats_cde.py`.
- Exploratory runners go in `scripts/` or `*/run_*.py`. Tests go in `tests/` only. Pytest collects `tests/`; a `scripts/test_*.py` is not the suite.
- Frontend edits stay in `stratforge/viz/web` (Vite + TypeScript).
- Module entries are `python -m stratforge.live`, `python -m stratforge.paper_family`, and `python -m stratforge.viz.projector`. Do not add a file-path entry for `strategies/live_node.py`.

## 2. Imports and paths

- A module that imports `strategies`, `ccxt_bridge`, or `risk_limits` imports `stratforge` first, or runs as `python -m ...`. That import puts the package directory on `sys.path`.
- Resolve `config/`, `data/`, and `results/` with `stratforge.state.state_root()`. Do not build those paths with `Path.parents`. Inside `cfm/` and `short/`, `parents[1]` is the package directory, not the repo root.
- Do not add a copy of a package module outside `stratforge/`. A copy on the state root shadows the install.
- New files, unit names, and path strings use `stratforge`. Leave the existing Vite `base` alone unless the task is that rename.

## 3. Constraints on the edit

- Order-placing code calls `risk_limits.check_order()`. Do not add a bypass or a second gate. Do not call `load_live_secrets()` on import or from paper or research modules. The live gate stays where it is (`LIVE_TRADING`, `SIMULATION_MODE`, `data/LIVE_APPROVED`, `COINBASE_PORTFOLIO_ID`).
- Journal writes append, flush, and fsync. `restore_from_journal()` reads and does not rewrite the file. Do not change `BOOK_SYNC` entry `ts` or `fee` handling unless a test in `tests/` specifies that behavior.
- A closed bucket emits once. Dedup is at emit time (`_last_emitted`). Do not add an ingest path that adds volume for a candle already folded into the open bucket.
- `apply_enabled=False` still updates engine state. It must not call `executor.apply`. Do not skip `on_bar` to make a check read-only.
- Stored and computed timestamps are timezone-aware UTC. ET conversion stays in `display_time.py`.
- On the live branch of `_fill_economics`, missing venue `cost` or `fee` remains `ORDER_INCOMPLETE_FILL`. Do not substitute `fee_rate` there. Estimation stays in the dry-run branch.

## 4. Hygiene

- Production modules keep type hints: `strategies/`, `ccxt_bridge.py`, `risk_limits.py`, `cfm/ledger.py`, `short/ledger.py`.
- Before deleting a helper, confirm zero callers and update tests that name it.
- Do not add a comment that restates the code.
- A comment stays only when it records a constraint the code cannot show.
- Do not delete an existing comment unless that constraint now lives in a test or a name.
- Use feature branches and create pull requests on github.
- Only merge to main after tests running on pull request on green.

## 5. Verify the change

- `.venv/bin/pytest` — no network, no exchange calls.
- If `stratforge/viz/web` changed: `npm run build` in that directory.
- If a check could not run, say that first.
