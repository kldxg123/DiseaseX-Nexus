import json
import sys
from pathlib import Path


payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
summary = []
for item in payload["results"]:
    latest = item.get("latest_pseudo") or {}
    baseline = item.get("latest_best_baseline") or {}
    config = item.get("config") or {}
    summary.append(
        {
            "track_key": item["track_key"],
            "pre_gate": item.get("pre_gate", False),
            "selection_median_wape": item.get("selection_median_wape"),
            "selection_worst_wape": item.get("selection_worst_wape"),
            "latest_pseudo_wape": latest.get("wape"),
            "latest_pseudo_agreement_pct": latest.get("agreement_pct"),
            "latest_per_horizon_wape": latest.get("per_horizon_wape"),
            "latest_baseline_wape": baseline.get("wape"),
            "latest_improvement": item.get("latest_improvement_vs_baseline"),
            "family": config.get("family"),
            "loss": config.get("loss"),
            "hidden_values_read": item.get("hidden_values_read"),
        }
    )
print(json.dumps(summary, ensure_ascii=False, indent=2))
