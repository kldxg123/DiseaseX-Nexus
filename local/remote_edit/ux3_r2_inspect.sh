#!/usr/bin/env bash
set -euo pipefail
cd USER_HOME/projects/openclaw
grep -R -n -E 'def .*graph|source_layer|advisory_rules|registry_fallback|lower_value|upper_value|demo-shadow-run|recommendation-guidance' \
  src/epidemic_core/forecast_model_kg \
  src/epidemic_core/dashboard/static \
  tests/test_ux3_r1_shadow_closure.py \
  scripts/kg_ux3_r1_closure_report.py | head -240 || true
