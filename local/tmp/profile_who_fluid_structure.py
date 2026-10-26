from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


RAW = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/runtime/fresh_source/who_fluid_frozen_20260814.csv")
METRICS = ("ILI_CASE", "ILI_OUTPATIENTS", "SARI_CASE", "SARI_INPATIENTS")


age_rows: Counter[str] = Counter()
age_countries: dict[str, set[str]] = defaultdict(set)
age_weeks: dict[str, set[str]] = defaultdict(set)
metric_nonempty: dict[str, Counter[str]] = {metric: Counter() for metric in METRICS}
country_weeks: dict[str, set[str]] = defaultdict(set)

with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
        age = (row.get("AGEGROUP_CODE") or "<EMPTY>").strip()
        country = (row.get("COUNTRY_CODE") or "<EMPTY>").strip()
        week = (row.get("ISOYW") or "").strip()
        age_rows[age] += 1
        age_countries[age].add(country)
        if week:
            age_weeks[age].add(week)
            country_weeks[country].add(week)
        for metric in METRICS:
            if (row.get(metric) or "").strip() not in {"", "NA", "N/A", "-"}:
                metric_nonempty[metric][age] += 1

payload = {
    "values_emitted": False,
    "country_count": len(country_weeks),
    "age_groups": [
        {
            "agegroup_code": age,
            "row_count": count,
            "country_count": len(age_countries[age]),
            "distinct_iso_weeks": len(age_weeks[age]),
            "metric_nonempty_counts": {metric: metric_nonempty[metric][age] for metric in METRICS},
        }
        for age, count in age_rows.most_common()
    ],
    "countries_with_at_least_445_distinct_weeks": sum(len(weeks) >= 445 for weeks in country_weeks.values()),
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
