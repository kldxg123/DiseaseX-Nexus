#!/usr/bin/env bash
set -euo pipefail

PROJECT="${1:-USER_HOME/projects/openclaw}"

echo "HOSTNAME=$(hostname)"
echo "USER=$(id -un)"
echo "UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

pids="$(pgrep -f 'uvicorn.*epidemic_core.dashboard.app' || true)"
echo "DASH_PIDS=${pids//$'\n'/ }"
for p in $pids; do
  echo "PID=$p"
  readlink -f "/proc/$p/cwd" || true
  tr '\0' ' ' < "/proc/$p/cmdline" || true
  echo
done

ss -ltnp | grep -E ':(8020|8021|8010|55432|8130) ' || true

cd "$PROJECT"
echo "PWD=$(pwd -P)"
git rev-parse --is-inside-work-tree 2>/dev/null || true
git rev-parse --abbrev-ref HEAD 2>/dev/null || true
git rev-parse HEAD 2>/dev/null || true
git status --short 2>/dev/null | head -80 || true

curl -fsS -o /tmp/r2_healthz.txt -w 'healthz=%{http_code}\n' http://127.0.0.1:8020/healthz || true
curl -fsS -o /tmp/r2_readyz.txt -w 'readyz=%{http_code}\n' http://127.0.0.1:8020/readyz || true
python3 - <<'PY'
for f in ['/tmp/r2_healthz.txt', '/tmp/r2_readyz.txt']:
    try:
        s = open(f, encoding='utf-8').read()
        print(f, s[:800])
    except Exception as e:
        print(f, e)
PY
