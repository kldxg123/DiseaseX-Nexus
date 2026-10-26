#!/usr/bin/env bash
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/source_promotion_worker/_common.sh
[[ -f "$PIDFILE" ]]&&kill -0 "$(<"$PIDFILE")" 2>/dev/null
