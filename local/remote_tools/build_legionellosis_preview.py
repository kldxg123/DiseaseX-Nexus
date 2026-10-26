from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("USER_HOME/projects/openclaw")
REPORT = ROOT / "reports/us_legionellosis_all_forecasts_v1_20260729T160650Z_90fbc361"
V7_RUN = REPORT / "artifacts/detail_txt_validation_v7/v7_20260731T023024Z"
OUT = ROOT / "storage/preview/us_legionellosis"


def sha_b(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_b(path.read_bytes())


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    prev22 = load(V7_RUN / "PREVIOUS_22_TXT_REVALIDATION_V7.json")
    all20 = load(V7_RUN / "ALL_20_EVIDENCE_REVALIDATION_V7.json")
    coverage = load(V7_RUN / "RECENT_104_DYNAMIC_COVERAGE_V7.json")
    remaining = load(V7_RUN / "REMAINING_RECENT_37_CANDIDATES_V7.json")

    validated_by_week: dict[str, dict[str, Any]] = {}
    for item in prev22["items"]:
        week = item["week"]
        if item["status"] == "PASS" and "2024-W09" <= week <= "2026-W07":
            validated_by_week[week] = {
                "week": week,
                "week_ending_date": item["parsed"]["txt_week_ending_date"],
                "current_week_raw": item["parsed"]["current_week_raw"],
                "current_week_numeric": item["parsed"]["current_week_numeric"],
                "current_week_semantic": item["parsed"]["current_week_semantic"],
                "cdc_pid": item["pid"],
                "validation_status": "VALIDATED",
                "validation_source": "previous_22_revalidated_v7",
                "txt_sha256": item["txt_sha256"],
            }
    for item in all20["items"]:
        if item["status"] == "PASS":
            validated_by_week[item["week"]] = {
                "week": item["week"],
                "week_ending_date": item["parsed"]["txt_week_ending_date"],
                "current_week_raw": item["current_week_raw"],
                "current_week_numeric": item["current_week_numeric"],
                "current_week_semantic": item["current_week_semantic"],
                "cdc_pid": item["pid"],
                "validation_status": "VALIDATED",
                "validation_source": "batch_1_revalidated_v7",
                "txt_sha256": item["txt_sha256"],
            }

    pending_by_week = {item["title_week_key"]: item for item in remaining["items"]}
    matrix = []
    for week in coverage["missing_metadata_week_keys"] + coverage["missing_metadata_week_keys"]:
        pass
    # Preserve the original chronological target order by reconstructing it from
    # present + missing status in the V7 coverage object.
    target_weeks = []
    start_year, start_week = 2024, 9
    # V7 already verified the 104-week range; regenerate the keys without values.
    year, week = start_year, start_week
    for _ in range(104):
        target_weeks.append(f"{year:04d}-W{week:02d}")
        week += 1
        if week > (53 if year == 2025 else 52):
            year += 1
            week = 1
    missing_set = set(coverage["missing_metadata_week_keys"])
    for week_key in target_weeks:
        if week_key in validated_by_week:
            row = dict(validated_by_week[week_key])
            row["coverage_status"] = "validated"
        elif week_key in pending_by_week:
            p = pending_by_week[week_key]
            row = {
                "week": week_key,
                "week_ending_date": p["title_week_ending_date"],
                "current_week_raw": None,
                "current_week_numeric": None,
                "current_week_semantic": "not_validated",
                "cdc_pid": p["pid"],
                "validation_status": "PENDING_VALIDATION",
                "coverage_status": "pending_validation",
                "canonical_detail_url": p["canonical_detail_url"],
            }
        elif week_key in missing_set:
            row = {
                "week": week_key,
                "week_ending_date": None,
                "current_week_raw": None,
                "current_week_numeric": None,
                "current_week_semantic": "metadata_missing",
                "cdc_pid": None,
                "validation_status": "METADATA_MISSING",
                "coverage_status": "metadata_missing",
            }
        else:
            row = {
                "week": week_key,
                "week_ending_date": None,
                "current_week_raw": None,
                "current_week_numeric": None,
                "current_week_semantic": "not_validated",
                "cdc_pid": None,
                "validation_status": "PENDING_VALIDATION",
                "coverage_status": "pending_validation",
            }
        matrix.append(row)

    validated = sorted(validated_by_week.values(), key=lambda x: x["week"])
    payload = {
        "generated_at_utc": utcnow(),
        "dataset_key": "preview-us-legionellosis-cdc-nndss-current-week-v1",
        "title": "United States Legionellosis weekly current week preview",
        "country": {"iso2": "US", "iso3": "USA", "name_en": "United States", "name_zh": "美国"},
        "disease": {"slug": "legionellosis", "name_en": "Legionellosis", "name_zh": "军团菌病"},
        "metric": {"code": "current_week_cases", "label": "Current week reported cases"},
        "source": {
            "publisher": "CDC",
            "system": "NNDSS",
            "series": "National Notifiable Diseases: Infectious Weekly Tables",
            "provisional": True,
        },
        "preview_notice": "预览数据：仅展示已验证周，尚未形成完整104周序列。",
        "usage_restrictions": {
            "trusted": False,
            "final": False,
            "active_pointer_updated": False,
            "training_or_prediction_allowed": False,
            "missing_weeks_imputed": False,
            "interpolated": False,
            "zero_filled": False,
        },
        "coverage": {
            "target_weeks": 104,
            "range_start": "2024-W09",
            "range_end": "2026-W07",
            "validated": len(validated),
            "pending_validation": len(remaining["items"]),
            "metadata_missing": len(coverage["missing_metadata_week_keys"]),
        },
        "validated_points": validated,
        "coverage_matrix": matrix,
        "lineage": {
            "v6_zip_sha256": "23687999053058e5002ad7a267eb63e20065f29c3d7d63ea4fb328900b068849",
            "v7_zip_sha256": "cd0225ca48af2209855974695daf0f0b6951ebfa958516c651ed2b4609ea6e96",
            "v7_status": "PARTIAL_RECENT_104_DETAIL_TXT_VALIDATION_BATCH_1_V7_OFFLINE_RECONCILED_37_PENDING_32_METADATA_WEEKS_MISSING",
        },
    }
    if payload["coverage"] != {
        "target_weeks": 104,
        "range_start": "2024-W09",
        "range_end": "2026-W07",
        "validated": 35,
        "pending_validation": 37,
        "metadata_missing": 32,
    }:
        raise SystemExit(f"coverage mismatch: {payload['coverage']}")
    if len(matrix) != 104:
        raise SystemExit(f"matrix length mismatch: {len(matrix)}")
    if any(x["coverage_status"] != "validated" and x["current_week_numeric"] is not None for x in matrix):
        raise SystemExit("non-validated point has numeric value")
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "preview_us_legionellosis_weekly_current_week_v1.json"
    csv_path = OUT / "preview_us_legionellosis_weekly_current_week_v1.csv"
    json_bytes = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    json_path.write_bytes(json_bytes)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        fields = [
            "week",
            "week_ending_date",
            "current_week_raw",
            "current_week_numeric",
            "current_week_semantic",
            "cdc_pid",
            "validation_status",
            "coverage_status",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{k: row.get(k) for k in fields} for row in matrix])
    receipt = {
        "generated_at_utc": payload["generated_at_utc"],
        "json_path": str(json_path.relative_to(ROOT)),
        "json_sha256": sha_b(json_bytes),
        "csv_path": str(csv_path.relative_to(ROOT)),
        "csv_sha256": sha_file(csv_path),
        "coverage": payload["coverage"],
    }
    (OUT / "preview_us_legionellosis_weekly_current_week_v1.receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
