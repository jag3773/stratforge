#!/usr/bin/env bash
# Commit + push tracked SRE daily reports only. No-op if clean.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mapfile -t files < <(ls -1 data/sre_daily_latest.md data/sre_daily_reports/*.md 2>/dev/null || true)
if [[ ${#files[@]} -eq 0 ]]; then
  exit 0
fi
if grep -E -n -- 'BEGIN (EC |RSA )?PRIVATE KEY|COINBASE_API_SECRET=' "${files[@]}"; then
  echo "commit_sre_reports: refusing secret-like material" >&2
  exit 1
fi

git add -- data/sre_daily_latest.md data/sre_daily_reports
if git diff --cached --quiet; then
  exit 0
fi
day="$(date -u +%F)"
git commit -m "docs(sre): daily site check ${day}"
git push origin HEAD
