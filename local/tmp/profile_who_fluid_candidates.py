from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


RAW = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/runtime/fresh_source/who_fluid_frozen_20260814.csv")
METRICS = {
    "ili_rate_per_1000_outpatients": ("ILI_CASE", "ILI_OUTPATIENTS"),
    "sari_rate_per_1000_inpatients": ("SARI_CASE", "SARI_INPATIENTS"),
}


def number(text: str | None) -> float | None:
    value = (text or "").strip()
    if value in {"", "NA", "N/A", "-"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def week(text: str | None) -> date | None:
    value = (text or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


series: dict[tuple[str, str, str], dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
names: dict[str, str] = {}
with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
        if (row.get("AGEGROUP_CODE") or "").strip().casefold() != "all":
            continue
        country = (row.get("COUNTRY_CODE") or "").strip()
        period = week(row.get("ISO_WEEKSTARTDATE"))
        if not country or period is None:
            continue
        names[country] = (row.get("COUNTRY_AREA_TERRITORY") or country).strip()
        for metric, (numerator_col, denominator_col) in METRICS.items():
            numerator = number(row.get(numerator_col))
            denominator = number(row.get(denominator_col))
            if numerator is None or denominator is None or denominator <= 0 or numerator < 0:
                continue
            series[(country, names[country], metric)][period].append(1000.0 * numerator / denominator)

profiles: list[dict[str, object]] = []
for (country, name, metric), by_week in sorted(series.items()):
    conflicts = sum(1 for values in by_week.values() if max(values) - min(values) > 1e-12)
    periods = sorted(by_week)
    if not periods:
        continue
    calendar_weeks = ((periods[-1] - periods[0]).days // 7) + 1
    coverage = len(periods) / calendar_weeks
    hidden_start = periods[-1] - timedelta(weeks=51)
    hidden_present = sum(period >= hidden_start for period in periods)
    profiles.append(
        {
            "country_code": country,
            "country_name": name,
            "metric": metric,
            "observed_weeks": len(periods),
            "calendar_weeks": calendar_weeks,
            "coverage": coverage,
            "first_week": periods[0].isoformat(),
            "last_week": periods[-1].isoformat(),
            "hidden_calendar_weeks": 52,
            "hidden_present_weeks": hidden_present,
            "duplicate_conflict_weeks": conflicts,
            "eligible_structure": bool(
                calendar_weeks >= 52 * 7
                and coverage >= 0.80
                and hidden_present >= 45
                and conflicts == 0
            ),
        }
    )

eligible = [profile for profile in profiles if profile["eligible_structure"]]
print(
    json.dumps(
        {
            "values_emitted": False,
            "profile_count": len(profiles),
            "eligible_structure_count": len(eligible),
            "eligible": eligible,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
)
