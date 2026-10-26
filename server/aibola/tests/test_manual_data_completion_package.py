from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from src.reports.manual_data_completion_package import build_manual_data_completion_package


def test_manual_completion_package_copies_templates_and_excludes_forbidden_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    templates = tmp_path / "data/outputs/templates"
    templates.mkdir(parents=True)
    for name in [
        "drc_2018_2020_health_zone_case_template.csv",
        "uganda_2022_district_case_template.csv",
        "expert_error_review_queue.csv",
        "preference_pair_annotation_queue.jsonl",
    ]:
        (templates / name).write_text("sample\n", encoding="utf-8")
    result = build_manual_data_completion_package()
    zip_path = tmp_path / result["package_zip"]
    assert zip_path.exists()
    assert (tmp_path / "data/outputs/manual_completion_package/field_dictionary.csv").exists()
    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "drc_2018_2020_health_zone_case_template.csv" in names
    assert "manual_data_completion_readme.md" in names
    assert not any(name.startswith(("models/", "lora_weights/", "logs/")) for name in names)
