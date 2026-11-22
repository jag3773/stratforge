#!/usr/bin/env bash
# Offline smoke gate. Never enables LIVE. Never creates LIVE_APPROVED.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "missing .venv/bin/python" >&2
  exit 2
fi
export LIVE_TRADING=false SIMULATION_MODE=true
echo "=== pytest tests/ ==="
"$PY" -m pytest tests/
"$PY" "$ROOT/scripts/validate_dna.py"
echo "SMOKE OK"
