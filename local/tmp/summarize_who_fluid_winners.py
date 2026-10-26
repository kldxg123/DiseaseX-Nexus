from __future__ import annotations

import json
from pathlib import Path


payload = json.loads(Path("/tmp/who_fluid_rate_prescreen.json").read_text(encoding="utf-8"))
winners = []
for item in payload["results"]:
    if not item.get("pre_gate"):
        continue
    winners.append(
        {
            "track_key": item["track_key"],
            "config": item["config"],
            "config_sha256": item["config_sha256"],
            "selection_median_wape": item["selection_median_wape"],
            "selection_worst_wape": item["selection_worst_wape"],
            "selection_blocks": [
                {
                    "wape": block["wape"],
                    "agreement_pct": block["agreement_pct"],
                    "paired_n": block["paired_n"],
                    "per_horizon_wape": block["per_horizon_wape"],
                }
                for block in item["selection_blocks"]
            ],
            "pseudo_blind": item["pseudo_blind"],
            "pseudo_best_baseline": item["pseudo_best_baseline"],
            "pseudo_improvement_vs_baseline": item["pseudo_improvement_vs_baseline"],
            "prefix_calendar_weeks": item["prefix_calendar_weeks"],
            "prefix_nonnull": item["prefix_nonnull"],
            "hidden_start": item["hidden_start"],
            "hidden_end": item["hidden_end"],
            "hidden_present_weeks": item["hidden_present_weeks"],
            "hidden_values_parsed": item["hidden_values_parsed"],
            "source_sha256": item["source_sha256"],
        }
    )
print(json.dumps({"winners": winners}, sort_keys=True))
