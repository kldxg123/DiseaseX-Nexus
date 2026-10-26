#!/usr/bin/env bash
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/source_review/_common.sh
alive||exit 0;pid=$(<"$PIDFILE");kill "$pid";for _ in $(seq 1 30);do kill -0 "$pid" 2>/dev/null||{ rm -f "$PIDFILE";exit 0;};sleep 1;done;exit 1
