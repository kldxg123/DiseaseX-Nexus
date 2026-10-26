from __future__ import annotations

import csv
from pathlib import Path

from src.data.filled_case_template_ingestor import validate_case_templates


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_case_template_ingestor_accepts_valid_case_row(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_csv(
        tmp_path / "data/manual_completed/drc_2018_2020_health_zone_case_template.csv",
        [
            {
                "date": "2020-01-01",
                "country": "Democratic Republic of the Congo",
                "iso3": "COD",
                "health_zone": "Beni",
                "health_zone_standard": "Beni",
                "cum_cases": "10",
                "new_cases": "",
                "value_type": "cumulative",
                "metric_type": "case",
                "case_definition": "confirmed",
                "source_url": "https://example.org/report",
                "source_file": "",
                "outbreak_id": "drc_2018_2020",
            }
        ],
    )
    result = validate_case_templates()
    assert result["valid_case_rows"] == 1
    assert result["completed_drc_rows"] == 1
    assert result["case_validation_passed"] is True


def test_case_template_ingestor_rejects_bad_semantics_and_sensitive_columns(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_csv(
        tmp_path / "data/manual_completed/uganda_2022_district_case_template.csv",
        [
            {
                "date": "bad-date",
                "country": "Uganda",
                "iso3": "UGA",
                "district": "",
                "new_cases": "abc",
                "value_type": "unknown",
                "metric_type": "mixed",
                "source_url": "",
                "source_file": "",
                "outbreak_id": "",
                "patient_name": "forbidden",
            }
        ],
    )
    result = validate_case_templates()
    errors = result["file_reports"][0]["errors"]
    assert result["valid_case_rows"] == 0
    assert any("sensitive_columns" in item for item in errors)
    assert any("invalid_value_type" in item for item in errors)
