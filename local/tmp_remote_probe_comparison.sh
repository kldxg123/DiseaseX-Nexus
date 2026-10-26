#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/projects/openclaw"
if [[ -d .git ]]; then
  echo "GIT_WORKTREE=yes"
else
  echo "GIT_WORKTREE=no"
fi
git rev-parse HEAD
dirty_count="$(git status --porcelain=v1 | wc -l | tr -d ' ')"
echo "DIRTY_COUNT=${dirty_count}"
git log -1 --oneline
echo "PORTS"
ss -lnt 2>/dev/null | awk '$4 ~ /:(8020|55432)$/ {print $4}'
health="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8020/healthz || true)"
ready="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8020/readyz || true)"
echo "HEALTH=${health}"
echo "READY=${ready}"
echo "BENCHMARK_DIRS"
find reports -maxdepth 1 -type d \( -iname '*benchmark*' -o -iname '*comparison*' \) -printf '%f\n' | sort | tail -n 30
