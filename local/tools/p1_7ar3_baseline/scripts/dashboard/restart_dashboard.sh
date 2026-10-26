#!/usr/bin/env bash
set -euo pipefail
"$(dirname "$0")/stop_dashboard.sh"
"$(dirname "$0")/start_dashboard.sh"
