#!/usr/bin/env bash
set -euo pipefail
PROJECT=USER_HOME/autodl-tmp/epidemic-openclaw;RUNTIME=USER_HOME/autodl-tmp/epidemic-source-review-runtime
install -d -o epidemicreview -g epidemicreview -m 0750 "$RUNTIME/src/epidemic_core/discovery"
install -o epidemicreview -g epidemicreview -m 0644 "$PROJECT/src/epidemic_core/discovery/review_app.py" "$RUNTIME/src/epidemic_core/discovery/review_app.py"
install -o epidemicreview -g epidemicreview -m 0644 "$PROJECT/src/epidemic_core/discovery/__init__.py" "$RUNTIME/src/epidemic_core/discovery/__init__.py"
