from __future__ import annotations

import csv
from pathlib import Path

from src.reports.drc_manual_case_completion_pack import build_drc_manual_case_completion_pack
from src.reports.uganda_manual_case_completion_pack import build_uganda_manual_case_completion_pack


def _fields(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh).fieldnames or [])


def test_p1d_case_completion_templates_include_semantic_gates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    drc = build_drc_manual_case_completion_pack()
    uganda = build_uganda_manual_case_completion_pack()
    assert drc["training_allowed"] is False
    assert uganda["training_allowed"] is False
    drc_fields = _fields(tmp_path / "data/outputs/templates/drc_2018_2020_health_zone_case_template.csv")
    uganda_fields = _fields(tmp_path / "data/outputs/templates/uganda_2022_district_case_template.csv")
    assert "health_zone_standard" in drc_fields
    assert "district" in uganda_fields
    assert "value_type" in drc_fields and "metric_type" in uganda_fields
