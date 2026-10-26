from __future__ import annotations

import json
from pathlib import Path

from src.data.external_case_deduplication import deduplicate_external_cases
from src.data.external_harmonization_common import read_csv_auto


def test_dedup_detects_original_overlap_without_additive_training(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    normalized = tmp_path / "normalized.csv"
    normalized.write_text(
        "date,country,adm1,adm2,metric_type,case_status,value,is_cumulative,is_incident,source_scope,usage,duplicate_policy,source_file\n"
        "2014-09-01,Guinea,,,case,confirmed,10,True,False,west_africa_2014_2016,validation_candidate,validation_only_not_additive,test.csv\n",
        encoding="utf-8",
    )
    processed = tmp_path / "data/processed"
    processed.mkdir(parents=True)
    (processed / "ebola_cases_daily.parquet.jsonl").write_text(
        json.dumps({"date": "2014-09-01", "country": "Guinea", "adm1": "", "adm2": "", "new_cases": 2}) + "\n",
        encoding="utf-8",
    )
    result = deduplicate_external_cases(normalized, processed)
    rows = read_csv_auto("data/processed/external_features/external_case_daily_deduplicated.csv")
    assert result["status"] == "ok"
    assert result["duplicate_with_original_count"] == 1
    assert rows[0]["dedup_action"] == "validation_only_not_added_to_panel"
    assert rows[0]["use_for_external_panel"] == "False"
