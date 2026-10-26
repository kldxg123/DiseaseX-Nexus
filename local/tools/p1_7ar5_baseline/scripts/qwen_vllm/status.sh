#!/usr/bin/env bash
set -euo pipefail
source 'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/qwen_vllm/_common.sh'
[[ -f "$PIDFILE" ]]; pid=$(<"$PIDFILE"); kill -0 "$pid"
grep -qx '0100007F:1F40' <(awk 'NR>1 && $4=="0A" {print $2}' /proc/net/tcp)
! grep -q ':1F40$' <(awk 'NR>1 && $4=="0A" {print $2}' /proc/net/tcp6)
'USER_HOME/autodl-tmp/epidemic-openclaw/scripts/qwen_vllm/health_check.sh'
