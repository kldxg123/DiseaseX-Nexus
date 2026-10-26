#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/_common.sh"
[[ ${EUID} -ne 0 ]] || { echo 'dashboard runtime must be installed by the project user' >&2; exit 1; }
install -d -m 0750 "$RUNTIME" "$RUNTIME/src" "$RUNTIME/src/epidemic_core"
mkdir -p "$RUNTIME/run" "$RUNTIME/log" "$RUNTIME/secrets"
chmod 0700 "$RUNTIME/run" "$RUNTIME/log" "$RUNTIME/secrets"
install -m 0640 "$PROJECT_ROOT/src/epidemic_core/__init__.py" "$RUNTIME/src/epidemic_core/__init__.py"
install -d -m 0750 "$RUNTIME/src/epidemic_core/dashboard" "$RUNTIME/src/epidemic_core/dashboard/static"
find "$PROJECT_ROOT/src/epidemic_core/dashboard" -maxdepth 1 -type f -name '*.py' -print0 |
  while IFS= read -r -d '' file; do
    install -m 0640 "$file" "$RUNTIME/src/epidemic_core/dashboard/$(basename "$file")"
  done
find "$PROJECT_ROOT/src/epidemic_core/dashboard/static" -maxdepth 1 -type f -print0 |
  while IFS= read -r -d '' file; do
    install -m 0640 "$file" "$RUNTIME/src/epidemic_core/dashboard/static/$(basename "$file")"
  done
install -d -m 0750 "$RUNTIME/src/epidemic_core/p1_10"
find "$PROJECT_ROOT/src/epidemic_core/p1_10" -maxdepth 1 -type f -name '*.py' -print0 |
  while IFS= read -r -d '' file; do
    install -m 0640 "$file" "$RUNTIME/src/epidemic_core/p1_10/$(basename "$file")"
  done
install -d -m 0750 "$RUNTIME/src/epidemic_core/ebola"
find "$PROJECT_ROOT/src/epidemic_core/ebola" -maxdepth 1 -type f -name '*.py' -print0 |
  while IFS= read -r -d '' file; do
    install -m 0640 "$file" "$RUNTIME/src/epidemic_core/ebola/$(basename "$file")"
  done
install -d -m 0750 "$RUNTIME/src/epidemic_core/p1_12"
find "$PROJECT_ROOT/src/epidemic_core/p1_12" -maxdepth 1 -type f -name '*.py' -print0 |
  while IFS= read -r -d '' file; do
    install -m 0640 "$file" "$RUNTIME/src/epidemic_core/p1_12/$(basename "$file")"
  done
