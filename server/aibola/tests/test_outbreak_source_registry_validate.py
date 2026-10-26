from __future__ import annotations

from pathlib import Path

from src.data_acquisition.outbreak_risk_collectors import validate_outbreak_source_registry


def test_outbreak_source_registry_blocks_missing_governance_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    config = tmp_path / "configs/outbreak_risk_sources.yaml"
    config.write_text(
        "sources:\n"
        "  bad:\n"
        "    source_name: Bad Source\n"
        "seed_events:\n"
        "  - outbreak_id: e1\n"
        "    country: Guinea\n"
        "    iso3: GIN\n",
        encoding="utf-8",
    )
    result = validate_outbreak_source_registry(config.as_posix())
    assert result["status"] == "blocked"
    assert any("missing_" in item["problem"] for item in result["problems"])
