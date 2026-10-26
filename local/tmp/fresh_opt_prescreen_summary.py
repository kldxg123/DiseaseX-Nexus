import json
import sys
from pathlib import Path


payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
summary = []
for item in payload["results"]:
    best = item.get("best") or {}
    baseline = item.get("best_baseline") or {}
    summary.append(
        {
            "track_key": item["track_key"],
            "pre_gate": item.get("pre_gate", False),
            "median_wape": best.get("median_wape"),
            "worst_wape": best.get("worst_wape"),
            "latest_pseudo_wape": best.get("latest_pseudo_wape"),
            "baseline_median_wape": baseline.get("median_wape"),
            "improvement": best.get("improvement_vs_best_baseline"),
            "method": (best.get("config") or {}).get("mode"),
            "years": (best.get("config") or {}).get("years"),
            "window": (best.get("config") or {}).get("recent_window"),
            "hidden_values_read": item.get("hidden_values_read"),
        }
    )
print(json.dumps(summary, ensure_ascii=False, indent=2))
