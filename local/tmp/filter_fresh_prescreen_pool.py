from __future__ import annotations

import json
from pathlib import Path


source = Path("/tmp/fresh_all_prescreen.json")
target = Path("/tmp/fresh_target_prescreen.json")
payload = json.loads(source.read_text(encoding="utf-8"))
rows = [item for item in payload["results"] if item.get("status") == "PRESCREENED_TUNING_ONLY"]


def score(item: dict[str, object]) -> tuple[float, float, str]:
    pseudo = item.get("pseudo_blind") or {}
    assert isinstance(pseudo, dict)
    median = float(item.get("selection_median_wape") or 999)
    pseudo_wape = float(pseudo.get("wape") or 999)
    worst = float(item.get("selection_worst_wape") or 999)
    return (0.55 * median + 0.35 * pseudo_wape + 0.10 * min(worst, 2.0), worst, str(item["track_key"]))


selected = sorted(rows, key=score)[:8]
filtered = {**payload, "candidate_count": len(selected), "pre_gate_count": 0, "selected_track_key": None, "results": selected}
target.write_text(json.dumps(filtered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"target": str(target), "selected_tracks": [item["track_key"] for item in selected]}))
