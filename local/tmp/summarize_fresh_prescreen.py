from __future__ import annotations

import json
from pathlib import Path


payload = json.loads(Path("/tmp/fresh_all_prescreen.json").read_text(encoding="utf-8"))
rows = [item for item in payload["results"] if item.get("status") == "PRESCREENED_TUNING_ONLY"]


def compact(item: dict[str, object]) -> dict[str, object]:
    pseudo = item.get("pseudo_blind") or {}
    assert isinstance(pseudo, dict)
    return {
        "track_key": item["track_key"],
        "config": item.get("config"),
        "selection_median_wape": item.get("selection_median_wape"),
        "selection_worst_wape": item.get("selection_worst_wape"),
        "pseudo_wape": pseudo.get("wape"),
        "pseudo_agreement_pct": pseudo.get("agreement_pct"),
        "pseudo_improvement_vs_baseline": item.get("pseudo_improvement_vs_baseline"),
        "hidden_values_read": item.get("hidden_values_read"),
        "prior_usage": item.get("prior_usage"),
    }


by_pseudo = sorted(
    rows,
    key=lambda item: (
        float((item.get("pseudo_blind") or {}).get("wape") or 999),
        float(item.get("selection_median_wape") or 999),
    ),
)
by_stability = sorted(
    rows,
    key=lambda item: (
        float(item.get("selection_median_wape") or 999),
        float(item.get("selection_worst_wape") or 999),
    ),
)
print(
    json.dumps(
        {
            "hidden_values_read": payload.get("hidden_values_read"),
            "candidate_count": payload.get("candidate_count"),
            "best_pseudo": [compact(item) for item in by_pseudo[:10]],
            "best_stability": [compact(item) for item in by_stability[:10]],
        },
        sort_keys=True,
    )
)
