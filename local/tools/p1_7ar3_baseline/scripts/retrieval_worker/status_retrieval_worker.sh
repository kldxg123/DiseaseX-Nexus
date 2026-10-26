#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/retrieval_worker/retrieval_worker_env.sh
pidfile="$PROJECT/storage/run/retrieval-worker/worker.pid";[[ -f "$pidfile" ]];pid=$(<"$pidfile");kill -0 "$pid";cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline");[[ "$cmd" == *"epidemic_core.retrieval.main"* ]];! ss -H -lntp | grep -Fq "pid=$pid,"
