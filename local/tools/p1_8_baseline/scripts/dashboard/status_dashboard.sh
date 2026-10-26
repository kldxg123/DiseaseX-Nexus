#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"
dashboard_alive && dashboard_health
