#!/usr/bin/env python3
"""Gap analysis for NO_ELIGIBLE profiles: verify gaps are genuine data insufficiency."""
import csv, sys
REPORT_DIR = sys.argv[1]
rows = list(csv.DictReader(open(f"{REPORT_DIR}/19_ALL_CANDIDATE_SCORES.csv")))
sel = list(csv.DictReader(open(f"{REPORT_DIR}/20_SELECTED_RECOMMENDATIONS.csv")))
profiles = list(csv.DictReader(open(f"{REPORT_DIR}/15_DATA_PROFILES.csv")))
pinfo = {f"{p['disease']}|{p['country']}|{p['indicator']}|{p['frequency']}": p for p in profiles}

# 20_SELECTED_RECOMMENDATIONS.csv contains only rank-1 ELIGIBLE rows
eligible_keys = {r["profile"] for r in sel}
all_keys = {r["profile"] for r in rows}
no_elig = sorted(all_keys - eligible_keys)
print("selected rows:", len(sel), "eligible:", len(eligible_keys), "no_elig:", len(no_elig))

analysis = []
for key in no_elig:
    cands = [c for c in rows if c["profile"] == key]
    scored = [c for c in cands if c["status"] == "SCORED"]
    scored.sort(key=lambda c: -float(c["total_score"]))
    p = pinfo.get(key, {})
    entry = {
        "profile": key,
        "n_obs": p.get("n_obs"), "series_class": p.get("semantics"),
        "zero_ratio": p.get("zero_ratio"),
        "scored_candidates": len(scored),
        "excluded_candidates": len(cands) - len(scored),
        "best": None,
        "genuine_gap": None,
    }
    if scored:
        b = scored[0]
        entry["best"] = {"adapter_id": b["adapter_id"], "total_score": b["total_score"],
                         "parameter_confidence": b["parameter_confidence"],
                         "dims": {d: b[d] for d in ("dim_A", "dim_B", "dim_C", "dim_D", "dim_E", "dim_F")},
                         "reasons": b["reasons"]}
    n = int(p.get("n_obs") or 0)
    entry["genuine_gap"] = (
        f"ULTRA_SHORT_SERIES n_obs={n} ({p.get('semantics')}): no honest probabilistic "
        f"time-series model can claim data-structure fit or identifiable parameters on "
        f"{n} observation(s); forcing score>70 would require fabricated evidence or score floors."
        if n <= 8 else "REVIEW_REQUIRED"
    )
    analysis.append(entry)
    print(key, "n_obs=", p.get("n_obs"), "class=", p.get("semantics"),
          "scored=", len(scored),
          "best=", entry["best"] and (entry["best"]["adapter_id"], entry["best"]["total_score"], entry["best"]["parameter_confidence"]))

import json
json.dump({"no_eligible_count": len(no_elig), "analysis": analysis},
          open(f"{REPORT_DIR}/22b_NO_ELIGIBLE_GAP_ANALYSIS.json", "w"), ensure_ascii=False, indent=1)
