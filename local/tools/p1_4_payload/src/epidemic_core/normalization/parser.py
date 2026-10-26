from __future__ import annotations

import csv
import datetime as dt
import decimal
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from epidemic_core.normalization.contract import Contract, CountryMapping, file_sha256

INTEGER = re.compile(r"^-?[0-9]+$")
MAX_LINE_LENGTH = 16 * 1024


class ParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRow:
    reported: dt.date
    country_code: str
    country_label: str
    who_region: str
    values: tuple[decimal.Decimal | None, decimal.Decimal | None, decimal.Decimal | None, decimal.Decimal | None]
    raw_values: tuple[str, str, str, str]
    exact_duplicate: bool = False


@dataclass(frozen=True)
class ParsedArtifact:
    total_rows: int
    country_rows: tuple[SourceRow, ...]
    selected_rows: tuple[SourceRow, ...]
    missing_values: int
    exact_duplicates: int
    unmapped_codes: tuple[str, ...]
    global_latest_date: dt.date


def _date(value: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError("INVALID_SOURCE_DATE") from exc
    if parsed.isoformat() != value:
        raise ParseError("INVALID_SOURCE_DATE")
    return parsed


def _number(value: str) -> decimal.Decimal | None:
    stripped = value.strip()
    if not stripped:
        return None
    if not INTEGER.fullmatch(stripped):
        raise ParseError("INVALID_SOURCE_INTEGER")
    return decimal.Decimal(stripped)


def verify_artifact(path: Path, expected_sha256: str, expected_size: int) -> None:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ParseError("ARTIFACT_INTEGRITY_ERROR") from exc
    if stat.st_size != expected_size or file_sha256(path) != expected_sha256:
        raise ParseError("ARTIFACT_INTEGRITY_ERROR")


def parse_artifact(
    path: Path,
    contract: Contract,
    mapping: CountryMapping,
    target_country_alpha2: str,
    start_date: dt.date,
    end_date: dt.date,
) -> ParsedArtifact:
    who_code = mapping.by_country.get(target_country_alpha2)
    if who_code is None:
        raise ParseError("UNMAPPED_TARGET_COUNTRY")
    csv.field_size_limit(MAX_LINE_LENGTH)
    total = 0
    missing = 0
    exact_duplicates = 0
    global_latest: dt.date | None = None
    target_rows: list[SourceRow] = []
    seen: dict[tuple[str, dt.date], tuple[str, ...]] = {}
    duplicate_keys: set[tuple[str, dt.date]] = set()
    unknown: set[str] = set()
    try:
        stream = path.open("r", encoding="utf-8-sig", newline="")
    except (OSError, UnicodeError) as exc:
        raise ParseError("ARTIFACT_INTEGRITY_ERROR") from exc
    try:
        reader = csv.reader(stream, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ParseError("SOURCE_SCHEMA_MISMATCH") from exc
        if tuple(header) != contract.expected_header:
            raise ParseError("SOURCE_SCHEMA_MISMATCH")
        for _line_number, csv_row in enumerate(reader, 2):
            if len(csv_row) != len(contract.expected_header):
                raise ParseError("SOURCE_SCHEMA_MISMATCH")
            if sum(len(x) for x in csv_row) > MAX_LINE_LENGTH:
                raise ParseError("SOURCE_ROW_TOO_LONG")
            reported = _date(csv_row[0])
            global_latest = reported if global_latest is None or reported > global_latest else global_latest
            key = (csv_row[1], reported)
            content = tuple(csv_row)
            prior = seen.get(key)
            if prior is not None:
                if prior != content:
                    raise ParseError("CONFLICTING_SOURCE_ROWS")
                exact_duplicates += 1
                duplicate_keys.add(key)
                continue
            seen[key] = content
            total += 1
            if csv_row[1] not in mapping.unmapped_codes and csv_row[1] not in mapping.by_country.values():
                unknown.add(csv_row[1])
            if csv_row[1] != who_code:
                continue
            numbers = tuple(_number(x) for x in csv_row[4:8])
            source = SourceRow(reported, csv_row[1], csv_row[2], csv_row[3], numbers, tuple(csv_row[4:8]))  # type: ignore[arg-type]
            target_rows.append(source)
    except (csv.Error, UnicodeError) as exc:
        raise ParseError("SOURCE_SCHEMA_MISMATCH") from exc
    finally:
        stream.close()
    if global_latest is None:
        raise ParseError("SOURCE_SCHEMA_MISMATCH")
    selected: list[SourceRow] = []
    for source_row in sorted(target_rows, key=lambda x: x.reported):
        if start_date <= source_row.reported <= end_date:
            duplicate = (source_row.country_code, source_row.reported) in duplicate_keys
            selected.append(SourceRow(source_row.reported, source_row.country_code, source_row.country_label, source_row.who_region, source_row.values, source_row.raw_values, duplicate))
            missing += sum(x is None for x in source_row.values)
    return ParsedArtifact(total, tuple(sorted(target_rows, key=lambda x: x.reported)), tuple(selected), missing, exact_duplicates, tuple(sorted(unknown | mapping.unmapped_codes)), global_latest)


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
