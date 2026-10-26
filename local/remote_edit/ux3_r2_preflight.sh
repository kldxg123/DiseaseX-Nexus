#!/usr/bin/env bash
set -euo pipefail
ROOT=USER_HOME/projects/openclaw
cd "$ROOT"
EXEC_ID="kg_p1_r3r_ux3_r1_r2_$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/${EXEC_ID}_preflight"
mkdir -p "$OUT"
{
  echo "execution_id=$EXEC_ID"
  echo "pwd=$(pwd)"
  echo "whoami=$(whoami)"
  printf 'git_root='
  git rev-parse --show-toplevel 2>/dev/null || true
  printf 'git_head='
  git rev-parse HEAD 2>/dev/null || true
  printf 'git_status_count='
  (git status --porcelain=v2 2>/dev/null || true) | wc -l
  printf 'dashboard_pids='
  (ps -eo pid,cmd | grep '[u]vicorn.*8020' | awk '{print $1}' | paste -sd, -) || true
  ss -ltnp 2>/dev/null | grep -E ':(8020|8021|8010|55432)' || true
} | tee "$OUT/context.txt"
python3 - <<'PY' > "$OUT/http_checks.json"
import json, urllib.request
out={}
for p in ['/healthz','/readyz','/forecast-model-knowledge-graph','/predictions','/fasttrack-forecast']:
    try:
        r=urllib.request.urlopen('http://127.0.0.1:8020'+p, timeout=5)
        out[p]={'status':r.status,'sample':r.read(300).decode('utf-8','replace')}
    except Exception as e:
        out[p]={'error':type(e).__name__+': '+str(e)}
print(json.dumps(out,ensure_ascii=False,indent=2))
PY
find . \( -name AGENTS.md -o -name README.md -o -iname '*deploy*' -o -iname '*runtime*' \) | sort | head -200 > "$OUT/doc_inventory.txt"
echo "OUT=$OUT"
