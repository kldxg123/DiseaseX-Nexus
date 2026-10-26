#!/usr/bin/env bash
set -euo pipefail
cleanup() { unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; }
trap cleanup EXIT INT TERM
if [[ ! -r /etc/network_turbo ]]; then exit 78; fi
source /etc/network_turbo >/dev/null
set +e
"$@"
rc=$?
set -e
cleanup
exit "$rc"
