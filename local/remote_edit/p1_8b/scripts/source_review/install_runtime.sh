#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P);RUNTIME="$PROJECT/../epidemic-source-review-runtime"
install -d -m 0750 "$RUNTIME/src/epidemic_core/discovery"
install -m 0644 "$PROJECT/src/epidemic_core/discovery/review_app.py" "$RUNTIME/src/epidemic_core/discovery/review_app.py"
install -m 0644 "$PROJECT/src/epidemic_core/discovery/review_labels.py" "$RUNTIME/src/epidemic_core/discovery/review_labels.py"
install -m 0644 "$PROJECT/src/epidemic_core/discovery/__init__.py" "$RUNTIME/src/epidemic_core/discovery/__init__.py"
install -d -m 0750 "$RUNTIME/src/epidemic_core/discovery/static"
find "$PROJECT/src/epidemic_core/discovery/static" -maxdepth 1 -type f -print0 |
  while IFS= read -r -d '' file; do
    install -m 0644 "$file" "$RUNTIME/src/epidemic_core/discovery/static/$(basename "$file")"
  done
