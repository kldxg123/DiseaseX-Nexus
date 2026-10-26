from __future__ import annotations

from pathlib import Path

from src.data.external_admin_matching import build_admin_matching


def test_admin_matching_uses_aliases(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    mapped = tmp_path / "mapped.csv"
    mapped.write_text(
        "date,country,adm1,adm2,metric_type_mapped,case_status,mapped_value,usage\n"
        "2021-01-01,Guinea,N'Zerekore,,case,confirmed,1,external_test_candidate\n",
        encoding="utf-8",
    )
    alias = tmp_path / "alias.yaml"
    alias.write_text(
        "thresholds:\n"
        "  min_admin_match_coverage: 0.9\n"
        "  max_needs_review_ratio: 0.1\n"
        "countries:\n"
        "  Guinea:\n"
        "    iso3: GIN\n"
        "    aliases: ['Guinea']\n"
        "adm1_aliases:\n"
        "  Guinea:\n"
        "    Nzerekore: [\"N'Zerekore\", \"N'Zérékoré\"]\n",
        encoding="utf-8",
    )
    result = build_admin_matching(mapped, alias)
    assert result["status"] == "ok"
    assert result["admin_match_coverage"] == 1.0
    assert result["needs_review_count"] == 0


def test_admin_matching_blocks_low_coverage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    mapped = tmp_path / "mapped.csv"
    mapped.write_text(
        "date,country,adm1,adm2,metric_type_mapped,case_status,mapped_value,usage\n"
        "2021-01-01,Unknownland,Nowhere,,case,confirmed,1,external_test_candidate\n",
        encoding="utf-8",
    )
    alias = tmp_path / "alias.yaml"
    alias.write_text(
        "thresholds:\n"
        "  min_admin_match_coverage: 0.9\n"
        "  max_needs_review_ratio: 0.1\n"
        "countries: {}\n",
        encoding="utf-8",
    )
    result = build_admin_matching(mapped, alias)
    assert result["status"] == "blocked"
