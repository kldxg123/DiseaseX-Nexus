#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"
[[ ${EUID} -eq 0 ]] || { echo 'dashboard runtime installation requires root' >&2; exit 1; }
install -d -o root -g epidemicui -m 0750 "$RUNTIME" "$RUNTIME/src" "$RUNTIME/src/epidemic_core"
install -d -o epidemicui -g epidemicui -m 0700 "$RUNTIME/run" "$RUNTIME/log" "$RUNTIME/secrets"
rm -rf "$RUNTIME/src/epidemic_core/dashboard"
install -d -o root -g epidemicui -m 0750 "$RUNTIME/src/epidemic_core/dashboard"
install -o root -g epidemicui -m 0640 "$PROJECT_ROOT/src/epidemic_core/__init__.py" "$RUNTIME/src/epidemic_core/__init__.py"
find "$PROJECT_ROOT/src/epidemic_core/dashboard" -maxdepth 1 -type f -name '*.py' -print0 |
  while IFS= read -r -d '' file; do
    install -o root -g epidemicui -m 0640 "$file" "$RUNTIME/src/epidemic_core/dashboard/$(basename "$file")"
  done
