from __future__ import annotations

from html.parser import HTMLParser

from crawler_agent.generic_parsers.types import (
    ParsedRecord,
    ParserResult,
    build_structured_text,
    has_numeric_signal,
    normalize_header,
    safe_record_metadata,
    squash,
    standardize_fields,
)
from crawler_agent.url_utils import normalize_url


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._in_cell = False
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized == "title":
            self._in_title = True
            self._title_parts = []
        elif normalized == "table":
            self._current_table = []
        elif normalized == "tr" and self._current_table is not None:
            self._current_row = []
        elif normalized in {"td", "th"} and self._current_row is not None:
            self._cell_parts = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "title":
            self._in_title = False
            title = squash(" ".join(self._title_parts))
            self.title = title or self.title
        elif normalized in {"td", "th"} and self._in_cell:
            value = squash(" ".join(self._cell_parts or []))
            if self._current_row is not None:
                self._current_row.append(value)
            self._cell_parts = None
            self._in_cell = False
        elif normalized == "tr" and self._current_table is not None and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif normalized == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_cell and self._cell_parts is not None:
            self._cell_parts.append(data)


def parse_html_table(
    content: str,
    *,
    source_name: str,
    url: str,
    country: str | None = None,
    disease: str | None = None,
    field_mapping: dict[str, str] | None = None,
) -> ParserResult:
    parser = _TableHTMLParser()
    parser.feed(content or "")
    warnings: list[str] = []
    table, table_number = _first_valid_table(parser.tables)
    if not table:
        return ParserResult("html_table", [], ["No valid HTML table found."], skipped_rows=0)
    headers = [normalize_header(cell) for cell in table[0]]
    records: list[ParsedRecord] = []
    skipped_rows = 0
    for row_index, row in enumerate(table[1:], start=1):
        record = {
            headers[index]: value
            for index, value in enumerate(row)
            if index < len(headers) and headers[index]
        }
        if not record:
            skipped_rows += 1
            continue
        structured = standardize_fields(
            record,
            country=country,
            disease=disease,
            source_type="official_dataset",
            field_mapping=field_mapping,
        )
        if "iso_week" not in structured and "week_label" not in structured:
            warnings.append(f"HTML table row {row_index} has no recognized week field.")
        if not has_numeric_signal(structured):
            warnings.append(f"HTML table row {row_index} has no recognized numeric surveillance field.")
        title = _record_title(source_name, structured, row_index, parser.title)
        records.append(
            ParsedRecord(
                source_name=source_name,
                source_type="official_dataset",
                parser_strategy="html_table",
                url=normalize_url(f"?row={row_index}&parser=html_table", base_url=url),
                title=title,
                text=build_structured_text(source_name, structured),
                country_detected=country,
                disease_detected=disease,
                language="en",
                structured_fields=structured,
                metadata=safe_record_metadata(
                    record,
                    row_number=row_index,
                    table_number=table_number,
                    content_kind="html_table",
                ),
            )
        )
    return ParserResult("html_table", records, warnings, skipped_rows=skipped_rows)


def _first_valid_table(tables: list[list[list[str]]]) -> tuple[list[list[str]] | None, int | None]:
    for index, table in enumerate(tables, start=1):
        if len(table) >= 2 and any(normalize_header(cell) for cell in table[0]):
            return table, index
    return None, None


def _record_title(source_name: str, structured: dict, index: int, page_title: str | None) -> str:
    week = structured.get("iso_week") or structured.get("week_label") or f"row {index}"
    region = structured.get("admin1") or structured.get("country") or "all regions"
    disease = structured.get("disease") or "surveillance"
    prefix = page_title or source_name
    return f"{prefix} {disease} record - {region} - {week}"
