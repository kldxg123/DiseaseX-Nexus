from __future__ import annotations

import csv
import io

from crawler_agent.generic_parsers.types import (
    ParsedRecord,
    ParserResult,
    build_structured_text,
    build_field_mapping,
    has_numeric_signal,
    normalize_header,
    safe_record_metadata,
    standardize_fields,
)
from crawler_agent.url_utils import normalize_url


def parse_csv_dataset(
    content: str,
    *,
    source_name: str,
    url: str,
    country: str | None = None,
    disease: str | None = None,
    field_mapping: dict[str, str] | None = None,
) -> ParserResult:
    warnings: list[str] = []
    try:
        reader = csv.DictReader(io.StringIO(content or ""))
        if not reader.fieldnames:
            return ParserResult("csv_dataset", [], ["CSV content has no header row."], skipped_rows=0)
        recognized_headers = {
            normalize_header(header)
            for header in reader.fieldnames
            if normalize_header(header) in build_field_mapping(field_mapping)
        }
        if not recognized_headers:
            return ParserResult(
                "csv_dataset",
                [],
                ["CSV header has no recognized surveillance fields."],
                skipped_rows=0,
            )
        rows = list(reader)
    except csv.Error as exc:
        return ParserResult("csv_dataset", [], [f"CSV parse failed: {exc}."], skipped_rows=0)

    records: list[ParsedRecord] = []
    skipped_rows = 0
    for index, row in enumerate(rows, start=1):
        if not any(str(value or "").strip() for value in row.values()):
            skipped_rows += 1
            continue
        structured = standardize_fields(
            row,
            country=country,
            disease=disease,
            source_type="official_dataset",
            field_mapping=field_mapping,
        )
        if "iso_week" not in structured and "week_label" not in structured:
            warnings.append(f"CSV row {index} has no recognized week field.")
        if not has_numeric_signal(structured):
            warnings.append(f"CSV row {index} has no recognized numeric surveillance field.")
        title = _record_title(source_name, structured, index)
        records.append(
            ParsedRecord(
                source_name=source_name,
                source_type="official_dataset",
                parser_strategy="csv_dataset",
                url=normalize_url(f"?row={index}&parser=csv_dataset", base_url=url),
                title=title,
                text=build_structured_text(source_name, structured),
                country_detected=country,
                disease_detected=disease,
                language="en",
                structured_fields=structured,
                metadata=safe_record_metadata(row, row_number=index, content_kind="csv_dataset"),
            )
        )
    return ParserResult("csv_dataset", records, warnings, skipped_rows=skipped_rows)


def _record_title(source_name: str, structured: dict, index: int) -> str:
    week = structured.get("iso_week") or structured.get("week_label") or f"row {index}"
    region = structured.get("admin1") or structured.get("country") or "all regions"
    disease = structured.get("disease") or "surveillance"
    return f"{source_name} {disease} record - {region} - {week}"
