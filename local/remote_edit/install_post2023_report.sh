#!/usr/bin/env bash
set -euo pipefail
old=USER_HOME/projects/openclaw/reports/post2023_epicasting_comparison_20260813
new=USER_HOME/projects/openclaw/reports/post2023_epicasting_comparison_20260813.finalizing
if [[ ! -f "$new/MANIFEST.sha256" ]]; then
  echo "FINALIZING_REPORT_MISSING" >&2
  exit 2
fi
rm -rf -- "$old"
mv -- "$new" "$old"
cd "$old"
sha256sum -c MANIFEST.sha256
cd ..
rm -f -- post2023_epicasting_comparison_20260813.zip
zip -q -r post2023_epicasting_comparison_20260813.zip post2023_epicasting_comparison_20260813
sha256sum post2023_epicasting_comparison_20260813.zip
