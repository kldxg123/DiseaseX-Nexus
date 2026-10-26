#!/usr/bin/env bash
set -euo pipefail

pids="$(pgrep -u dell -f 'postgres.*55432' || pgrep -f 'postgres.*55432' || true)"
echo "PIDS=${pids//$'\n'/ }"
for p in $pids; do
  echo "PID=$p"
  tr '\0' ' ' < "/proc/$p/cmdline" || true
  echo
  readlink -f "/proc/$p/cwd" || true
done

find USER_HOME/projects/openclaw -maxdepth 5 \( -type s -name '.s.PGSQL.55432' -o -type d -name '*postgres*' \) | head -120
