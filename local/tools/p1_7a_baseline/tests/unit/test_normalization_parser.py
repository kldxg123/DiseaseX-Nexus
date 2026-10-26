from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from epidemic_core.normalization.contract import Contract, CountryMapping, Versions
from epidemic_core.normalization.parser import ParseError, parse_artifact
from epidemic_core.normalization.service import _quality_flags, sha

HEADER = "Date_reported,Country_code,Country,WHO_region,New_cases,Cumulative_cases,New_deaths,Cumulative_deaths"


def contract() -> Contract:
    return Contract(
        Versions("transform-v1", "map-v1", "quality-v1", 1),
        tuple(HEADER.split(",")),
        {
            "New_cases": "cases_new",
            "Cumulative_cases": "cases_cumulative",
            "New_deaths": "deaths_new",
            "Cumulative_deaths": "deaths_cumulative",
        },
        "who",
        "https://example.test/data.csv",
        "https://example.test",
        "https://example.test/terms",
        "WHO",
        "No endorsement",
    )


def mapping() -> CountryMapping:
    return CountryMapping("map-v1", "a" * 64, {"FR": "FR"}, frozenset({"XK", "XXF"}))


def write(path: Path, rows: list[str], *, bom: bool = False, crlf: bool = False) -> None:
    separator = "\r\n" if crlf else "\n"
    text = separator.join([HEADER, *rows]) + separator
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode())


def test_bom_crlf_missing_zero_negative_and_inclusive_dates(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    write(
        path,
        [
            "2024-01-01,FR,France,EUR,0,10,,1",
            "2024-01-08,FR,France,EUR,-2,8,0,1",
            "2024-01-15,XK,Kosovo,EUR,1,1,0,0",
        ],
        bom=True,
        crlf=True,
    )
    parsed = parse_artifact(path, contract(), mapping(), "FR", dt.date(2024, 1, 1), dt.date(2024, 1, 8))
    assert [x.reported for x in parsed.selected_rows] == [dt.date(2024, 1, 1), dt.date(2024, 1, 8)]
    assert parsed.selected_rows[0].values[0] == 0
    assert parsed.selected_rows[0].values[2] is None
    assert parsed.selected_rows[1].values[0] == -2
    assert parsed.missing_values == 1
    assert "XK" in parsed.unmapped_codes


def test_exact_duplicate_is_deduplicated_and_flagged(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    row = "2024-01-01,FR,France,EUR,1,1,0,0"
    write(path, [row, row])
    parsed = parse_artifact(path, contract(), mapping(), "FR", dt.date(2024, 1, 1), dt.date(2024, 1, 1))
    assert parsed.total_rows == 1 and parsed.exact_duplicates == 1 and parsed.selected_rows[0].exact_duplicate


@pytest.mark.parametrize(
    "second",
    [
        "2024-01-01,FR,France,EUR,2,2,0,0",
        "2024-99-01,FR,France,EUR,1,1,0,0",
        "2024-01-08,FR,France,EUR,1.5,2,0,0",
    ],
)
def test_conflict_invalid_date_and_invalid_integer_fail(tmp_path: Path, second: str) -> None:
    path = tmp_path / "data.csv"
    write(path, ["2024-01-01,FR,France,EUR,1,1,0,0", second])
    with pytest.raises(ParseError):
        parse_artifact(path, contract(), mapping(), "FR", dt.date(2024, 1, 1), dt.date(2024, 12, 31))


def test_schema_and_unmapped_target_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("bad,header\n1,2\n")
    with pytest.raises(ParseError, match="SOURCE_SCHEMA_MISMATCH"):
        parse_artifact(path, contract(), mapping(), "FR", dt.date(2024, 1, 1), dt.date(2024, 1, 1))
    write(path, ["2024-01-01,FR,France,EUR,1,1,0,0"])
    with pytest.raises(ParseError, match="UNMAPPED_TARGET_COUNTRY"):
        parse_artifact(path, contract(), mapping(), "XK", dt.date(2024, 1, 1), dt.date(2024, 1, 1))


def test_no_observations_and_boundary_week_not_split(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    write(path, ["2024-01-07,FR,France,EUR,1,1,0,0"])
    parsed = parse_artifact(path, contract(), mapping(), "FR", dt.date(2024, 1, 8), dt.date(2024, 1, 14))
    assert parsed.selected_rows == ()


def test_quality_flags_preserve_revisions_gap_latest_and_delta() -> None:
    parsed_contract = contract()
    del parsed_contract
    from epidemic_core.normalization.parser import SourceRow

    previous = SourceRow(dt.date(2024, 1, 1), "FR", "France", "EUR", (10, 100, 2, 20), ("10", "100", "2", "20"))
    row = SourceRow(dt.date(2024, 1, 15), "FR", "France", "EUR", (-5, 90, -1, -2), ("-5", "90", "-1", "-2"), True)
    flags = _quality_flags(row, previous, row.reported)
    assert "RETROSPECTIVE_NEGATIVE_REVISION" in flags[0]
    assert "NEGATIVE_CUMULATIVE_VALUE" in flags[3]
    assert "CUMULATIVE_REVISION" in flags[1]
    assert "REPORTED_NEW_DIFFERS_FROM_CUMULATIVE_DELTA" in flags[0]
    assert "REPORTING_GAP_BEFORE" in flags[2]
    assert "LATEST_PERIOD_POSSIBLY_INCOMPLETE" in flags[3]
    assert "EXACT_DUPLICATE_SOURCE_ROW" in flags[0]


def test_canonical_hash_is_order_independent_and_has_no_uuid_or_clock() -> None:
    assert sha({"b": 2, "a": 1}) == sha({"a": 1, "b": 2})
