from __future__ import annotations

import csv
from pathlib import Path

from src.data.drc_health_zone_mapping import build_drc_health_zone_mapping


def test_drc_health_zone_mapping_matches_alias_and_reports_coverage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "configs/geo_alias_drc_health_zones.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "version: 1\n"
        "thresholds:\n"
        "  min_health_zone_mapping_coverage: 0.95\n"
        "  fuzzy_high_threshold: 0.88\n"
        "  fuzzy_low_threshold: 0.75\n"
        "country: {iso3: COD}\n"
        "health_zones:\n"
        "  - health_zone_standard: Beni\n"
        "    province_standard: North Kivu\n"
        "    adm1: North Kivu\n"
        "    adm2: Beni\n"
        "    lat: 0.49\n"
        "    lon: 29.47\n"
        "    aliases: [Beni, Beni HZ]\n",
        encoding="utf-8",
    )
    observed = tmp_path / "data/processed/external_v3/external_v3_case_observations_admin_matched.csv"
    observed.parent.mkdir(parents=True)
    with observed.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source_scope", "health_zone", "province"])
        writer.writeheader()
        writer.writerow({"source_scope": "drc_2018_2020", "health_zone": "Beni HZ", "province": "North Kivu"})
    result = build_drc_health_zone_mapping(config)
    assert result["mapping_coverage"] == 1.0
    assert result["drc_case_normalization_allowed"] is True
    assert (tmp_path / "data/processed/external_v3/drc_health_zone_mapping.csv").exists()
