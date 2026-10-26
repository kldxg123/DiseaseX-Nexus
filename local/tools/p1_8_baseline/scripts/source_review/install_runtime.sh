#!/usr/bin/env bash
set -euo pipefail
PROJECT=USER_HOME/autodl-tmp/epidemic-openclaw;RUNTIME=USER_HOME/autodl-tmp/epidemic-source-review-runtime
install -d -o epidemicreview -g epidemicreview -m 0750 "$RUNTIME/src/epidemic_core/discovery"
install -o epidemicreview -g epidemicreview -m 0644 "$PROJECT/src/epidemic_core/discovery/review_app.py" "$RUNTIME/src/epidemic_core/discovery/review_app.py"
install -o epidemicreview -g epidemicreview -m 0644 "$PROJECT/src/epidemic_core/discovery/review_labels.py" "$RUNTIME/src/epidemic_core/discovery/review_labels.py"
install -o epidemicreview -g epidemicreview -m 0644 "$PROJECT/src/epidemic_core/discovery/__init__.py" "$RUNTIME/src/epidemic_core/discovery/__init__.py"
install -d -o epidemicreview -g epidemicreview -m 0750 "$RUNTIME/src/epidemic_core/discovery/static"
find "$PROJECT/src/epidemic_core/discovery/static" -maxdepth 1 -type f -print0 |
  while IFS= read -r -d '' file; do
    install -o epidemicreview -g epidemicreview -m 0644 "$file" "$RUNTIME/src/epidemic_core/discovery/static/$(basename "$file")"
  done
