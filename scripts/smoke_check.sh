#!/usr/bin/env bash
# REAL smoke gate — run on this host with .venv.
# No Nautilus required. Never enables LIVE. Never creates LIVE_APPROVED.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "missing .venv/bin/python" >&2
  exit 2
fi
export LIVE_TRADING=false SIMULATION_MODE=true
if "$PY" -c "import pytest" >/dev/null 2>&1; then
  echo "=== pytest tests/ (real gate) ==="
  "$PY" -m pytest tests/ --ignore=archive
else
  echo "pytest missing — scripts/ shims (pip install -r requirements-dev.txt)"
  tests=(
    scripts/test_config_load.py
    scripts/test_paper_executor.py
    scripts/test_signal_parity.py
    scripts/test_phase2_execution.py
    scripts/test_risk_limits.py
    scripts/test_p0_live_safety.py
    scripts/test_live_reconcile_parse.py
    scripts/test_p1_ops.py
    scripts/test_chaos_journal.py
    scripts/test_admission.py
  )
  for t in "${tests[@]}"; do
    echo "=== $t ==="
    "$PY" "$ROOT/$t"
  done
fi
"$PY" "$ROOT/scripts/validate_dna.py"
echo "SMOKE OK"
