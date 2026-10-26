#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/qwen_vllm/_common.sh'
if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then curl -fsS "http://$HOST:$PORT/health" >/dev/null && exit 0; exit 1; fi
USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python -c 'import os,sys; p=sys.argv[1]; os.unlink(p) if os.path.exists(p) else None' "$PIDFILE"
ctx=${VLLM_MAX_MODEL_LEN:-32768}
started=$(date +%s)
nohup "$ENV/bin/vllm" serve "$MODEL" --served-model-name qwen3-8b-awq-local --host "$HOST" --port "$PORT" --quantization awq --dtype auto --max-model-len "$ctx" --gpu-memory-utilization 0.20 --max-num-seqs 4 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking":false}' >"$LOGS/vllm-server.log" 2>&1 &
pid=$!; printf '%s\n' "$pid" >"$PIDFILE"; printf '%s\n' "$ctx" >"$RUNTIME/max_model_len"; printf '%s\n' "$started" >"$RUNTIME/start_epoch"
for _ in $(seq 1 900); do if ! kill -0 "$pid" 2>/dev/null; then tail -n 80 "$LOGS/vllm-server.log"; exit 1; fi; if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then date +%s >"$RUNTIME/ready_epoch"; exit 0; fi; sleep 1; done
tail -n 80 "$LOGS/vllm-server.log"; exit 1
