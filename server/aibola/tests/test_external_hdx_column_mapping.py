from __future__ import annotations

from pathlib import Path

from src.data.external_hdx_column_mapping import build_hdx_column_mapping


def test_hdx_mapping_blocks_manual_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    staged = tmp_path / "staged.csv"
    staged.write_text(
        "date,country,adm1,adm2,metric,metric_type,value,source_file,source_scope,source_granularity,quality_flag\n"
        "2021-01-01,Guinea,,,,case,1,test.csv,guinea_2021_context,country_daily,\n",
        encoding="utf-8",
    )
    config = tmp_path / "mapping.yaml"
    config.write_text(
        "files:\n"
        "  test.csv:\n"
        "    source_scope: guinea_2021_context\n"
        "    usage: external_test_candidate\n"
        "    default_is_cumulative: true\n"
        "    needs_manual_review: true\n"
        "metric_rules:\n"
        "  - match_contains: ['case']\n"
        "    metric_type: case\n"
        "    case_status: confirmed\n"
        "    allowed_for_label: true\n",
        encoding="utf-8",
    )
    result = build_hdx_column_mapping(staged, config)
    assert result["status"] == "blocked"
    assert result["needs_manual_review_count"] == 1


def test_hdx_mapping_assigns_explicit_semantics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    staged = tmp_path / "staged.csv"
    staged.write_text(
        "date,country,adm1,adm2,metric,metric_type,value,source_file,source_scope,source_granularity,quality_flag\n"
        "2021-01-01,Guinea,,,,EBOLA_CONFIRMED_CASE,7,test.csv,guinea_2021_context,country_daily,\n",
        encoding="utf-8",
    )
    config = tmp_path / "mapping.yaml"
    config.write_text(
        "files:\n"
        "  test.csv:\n"
        "    source_scope: guinea_2021_context\n"
        "    usage: external_test_candidate\n"
        "    default_is_cumulative: true\n"
        "metric_rules:\n"
        "  - match_contains: ['confirmed']\n"
        "    metric_type: case\n"
        "    case_status: confirmed\n"
        "    allowed_for_label: true\n",
        encoding="utf-8",
    )
    result = build_hdx_column_mapping(staged, config)
    assert result["status"] == "ok"
    assert result["usage_counts"]["external_test_candidate"] == 1
