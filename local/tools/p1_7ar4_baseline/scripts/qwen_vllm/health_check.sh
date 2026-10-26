#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/qwen_vllm/_common.sh'
curl -fsS "http://$HOST:$PORT/health" >/dev/null
"$ENV/bin/python" -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/v1/models",timeout=10)); assert any(x["id"]=="qwen3-8b-awq-local" for x in d["data"])'
