from __future__ import annotations

import csv
import json
from pathlib import Path

from src.reports.manual_data_acquisition_templates import build_manual_templates


def test_manual_templates_write_required_files_and_rules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = build_manual_templates()
    assert result["status"] == "ok"
    drc_path = tmp_path / "data/outputs/templates/drc_2018_2020_case_template.csv"
    uganda_path = tmp_path / "data/outputs/templates/uganda_2022_case_template.csv"
    expert_path = tmp_path / "data/outputs/templates/expert_error_review_template.csv"
    preference_path = tmp_path / "data/outputs/templates/preference_pair_template.jsonl"
    assert drc_path.exists()
    assert uganda_path.exists()
    assert expert_path.exists()
    assert preference_path.exists()
    with drc_path.open("r", encoding="utf-8", newline="") as fh:
        fieldnames = csv.DictReader(fh).fieldnames
    assert "value_type" in fieldnames
    assert "metric_type" in fieldnames
    assert "source_url" in fieldnames
    preference = json.loads(preference_path.read_text(encoding="utf-8").splitlines()[0])
    assert preference["pii_removed"] is True
    assert "cannot be used for training" in result["rules"]["value_type"]
