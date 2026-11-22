#!/usr/bin/env bash
# Keep on-disk 5m history fresh. Silent on success.
set +e
cd "$(dirname "$0")/.." || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "HISTORY REFRESH FAILED: missing .venv/bin/python"
  exit 1
fi
OUT=$(.venv/bin/python -u scripts/refresh_recent_5m.py 2>&1)
RC=$?
if [ "$RC" -ne 0 ]; then
  echo "HISTORY REFRESH FAILED (rc=$RC) on $(date -u):"
  echo "$OUT" | tail -30
  exit 1
fi
exit 0
