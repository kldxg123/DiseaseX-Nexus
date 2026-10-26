from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from cleaner_agent.event_extraction import (
    extract_event_from_raw_document,
    extract_numbers_for_epidemic_events,
)


FIXTURES = Path("tests/fixtures")


def _raw_document(body_text: str):
    return SimpleNamespace(
        id=42,
        title="fixture",
        body_text=body_text,
        published_at=datetime(2025, 1, 30, tzinfo=UTC),
        fetched_at=datetime(2025, 1, 30, tzinfo=UTC),
        country_detected=None,
        disease_detected=None,
        source_registry=SimpleNamespace(reliability_score=0.9),
    )


def test_extract_numbers_for_epidemic_events_english_and_chinese() -> None:
    english = extract_numbers_for_epidemic_events(
        "12 cases, 12 confirmed cases, 5 suspected cases, 3 outbreaks, "
        "2 deaths, 4 hospitalizations, 100 tests, 8.5% positivity"
    )
    chinese = extract_numbers_for_epidemic_events(
        "报告12例，确诊10例，疑似2例，3起暴发，死亡1例，住院4例，检测100次，阳性率8.5%"
    )

    assert english["cases"] == 12
    assert english["confirmed_cases"] == 12
    assert english["suspected_cases"] == 5
    assert english["outbreaks_count"] == 3
    assert english["deaths"] == 2
    assert english["hospitalizations"] == 4
    assert english["tests"] == 100
    assert english["positivity_rate"] == 8.5
    assert chinese["cases"] == 12
    assert chinese["confirmed_cases"] == 10
    assert chinese["outbreaks_count"] == 3


def test_extract_event_from_english_fixture() -> None:
    text = (FIXTURES / "cleaner_norovirus_en.txt").read_text(encoding="utf-8")
    event = extract_event_from_raw_document(_raw_document(text))

    assert event is not None
    assert event.country == "United States"
    assert event.disease == "norovirus"
    assert event.event_date.isoformat() == "2025-01-31"
    assert event.cases == 12
    assert event.outbreaks_count == 2
    assert event.setting == "school"
    assert event.extraction_method == "rule_based_v1"
    assert event.provenance_json["raw_document_id"] == 42
    assert "numeric_evidence" in event.provenance_json


def test_extract_event_from_chinese_fixture() -> None:
    text = (FIXTURES / "cleaner_norovirus_zh.txt").read_text(encoding="utf-8")
    event = extract_event_from_raw_document(_raw_document(text))

    assert event is not None
    assert event.country == "United States"
    assert event.disease == "norovirus"
    assert event.cases == 12
    assert event.confirmed_cases == 10
    assert event.outbreaks_count == 3
    assert event.setting == "school"


def test_irrelevant_text_returns_none_and_missing_numbers_is_low_confidence() -> None:
    irrelevant = (FIXTURES / "cleaner_irrelevant.txt").read_text(encoding="utf-8")
    missing = (FIXTURES / "cleaner_missing_numbers.txt").read_text(encoding="utf-8")

    assert extract_event_from_raw_document(_raw_document(irrelevant)) is None
    event = extract_event_from_raw_document(_raw_document(missing))

    assert event is not None
    assert event.cases is None
    assert event.provenance_json["numeric_evidence"] == {}
    assert 0 <= event.confidence_score <= 1
