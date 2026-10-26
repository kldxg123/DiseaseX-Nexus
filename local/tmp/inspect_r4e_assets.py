from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")
FILES = [
    ROOT / "reports/r4e_cdc_ilinet_ingestion_20260806T1225Z/R4E_SOURCE_CONTRACT.json",
    ROOT / "reports/r4e_cdc_ilinet_ingestion_20260806T1225Z/R4E_NORMALIZED_DATA_PROFILE.json",
    ROOT / "reports/r4e_cdc_ilinet_ingestion_20260806T1225Z/R4E_MODEL_DATA_READINESS.json",
    ROOT / "reports/r4e_cdc_ilinet_ingestion_20260806T1225Z/R4E_SOURCE_PROVENANCE.json",
]

ALLOWED_KEYS = {
    "status",
    "source_id",
    "source_name",
    "publisher",
    "source_url",
    "official_url",
    "endpoint",
    "rows",
    "row_count",
    "normalized_row_count",
    "min_date",
    "max_date",
    "date_min",
    "date_max",
    "period_start_min",
    "period_start_max",
    "geography_count",
    "state_count",
    "metric",
    "metric_code",
    "granularity",
    "frequency",
    "model_ready",
    "ready",
    "reason",
    "sha256",
    "raw_sha256",
}


def filtered(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if key in ALLOWED_KEYS:
                result[key] = filtered(item)
            elif isinstance(item, (dict, list)):
                nested = filtered(item)
                if nested not in ({}, []):
                    result[key] = nested
        return result
    if isinstance(value, list):
        return [filtered(item) for item in value[:10]]
    return value


for path in FILES:
    print(f"FILE={path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR={type(exc).__name__}")
        continue
    print(json.dumps(filtered(payload), ensure_ascii=False, indent=2, sort_keys=True))
