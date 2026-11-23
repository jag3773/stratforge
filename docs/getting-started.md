# Getting Started

## Prerequisites

- Linux with systemd (Debian/Ubuntu amd64 in production)
- Python 3.13
- Coinbase public REST is enough for paper. LIVE needs a view+trade key (no withdraw) in `.env.live`, mode 600

## Installation

```bash
pip install stratforge
```

For a checkout, `uv venv --python 3.13 .venv` then `uv pip install -e .`.

## State directory

Set `STRATFORGE_STATE` to the directory that holds `config/` and `data/` before the process starts. The approval file, journals, and logs live there, not inside the package. Do not copy `strategies/` or `ccxt_bridge.py` into that directory.

## First run

```bash
export STRATFORGE_STATE=/path/to/state
mkdir -p "$STRATFORGE_STATE/data/raw" "$STRATFORGE_STATE/logs"
python -m stratforge.live --config config/live_paper.json
```

Expect `[node] requested_mode=paper`, a journal restore line, and warmed indicators. Fills wait for the next completed 4h close (8pm / midnight / 4am / 8am / noon / 4pm ET; one hour earlier in winter).

Do not run `strategies/live_node.py` from the state directory. That path is not the entry point, and a file sitting there shadows the package.

## Paper status

```bash
python -m stratforge.live --help
systemctl --user status stratforge-paper.service
```

The paper unit must exec `python -m stratforge.live`. Donch uses `python -m stratforge.paper_family`.

## Configuration

- `config/trend_rotation_25_max8_rotate.json` — production DNA
- `config/live_paper.json` — paper overlay (`_include` DNA)
- `config/live_micro.json` — example micro LIVE overlay
- `config/universe_live.json` — admitted symbols
- Halt: `data/TRADING_HALTED` or `data/TRADING_HALTED_{paper,live}` in the state directory
- LIVE also needs `LIVE_TRADING=true`, `SIMULATION_MODE=false`, and a `data/LIVE_APPROVED` file in the state directory

## Next

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [PROMOTE.md](PROMOTE.md)
