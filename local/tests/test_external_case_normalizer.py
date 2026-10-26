from __future__ import annotations

from pathlib import Path

from src.data.external_case_normalizer import normalize_external_cases
from src.data.external_harmonization_common import read_csv_auto


def test_case_normalizer_preserves_provenance_and_filters_unclear(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    matched = tmp_path / "matched.csv"
    matched.write_text(
        "date,country,adm1,adm2,metric_type_mapped,case_status,mapped_value,is_cumulative,is_incident,source_scope,usage,allowed_for_label,duplicate_policy,source_file,metric,admin_match_method,quality_flag\n"
        "2021-01-01,Guinea,Nzerekore,,case,confirmed,5,True,False,guinea_2021_context,external_test_candidate,True,add_if_no_original_overlap,test.csv,CONFIRMED,alias,\n"
        "2021-01-01,Guinea,Nzerekore,,,unknown,5,True,False,guinea_2021_context,external_test_candidate,False,add_if_no_original_overlap,test.csv,UNKNOWN,alias,\n",
        encoding="utf-8",
    )
    result = normalize_external_cases(matched)
    rows = read_csv_auto("data/processed/external_features/external_case_daily_normalized.csv")
    assert result["status"] == "ok"
    assert result["records"] == 1
    assert "source_file" in rows[0]["source_provenance"]
    assert rows[0]["metric_type"] == "case"
