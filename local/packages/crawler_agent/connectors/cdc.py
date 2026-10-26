from __future__ import annotations

import hashlib
import csv
import io
import json
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any

import httpx

from crawler_agent.access_policy import load_access_policies
from crawler_agent.connectors.base import (
    ConnectorDocument,
    document_from_html,
    text_from_record,
)
from crawler_agent.generic_parsers import parse_by_strategy, parser_result_to_connector_documents
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.url_utils import normalize_url

CDC_NOROSTAT_SOURCE_KEY = "cdc_norostat"
CDC_NOROSTAT_LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"
CDC_FLUVIEW_SOURCE_KEY = "cdc_fluview"


class CdcNoroStatConnector:
    connector_name = "cdc_norostat"

    def __init__(self) -> None:
        self.last_live_response_summary: dict[str, Any] | None = None

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        table_documents = _parse_norostat_table(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            live_fetch=False,
        )
        if table_documents:
            return table_documents
        return [
            document_from_html(
                connector_name=self.connector_name,
                source_name="CDC NoroSTAT",
                url=base_url,
                html=payload,
                metadata={"fixture_type": "html", "parse_mode": "html_summary"},
            )
        ]

    def fetch_live(
        self,
        *,
        url: str | None = None,
        allow_network: bool = False,
        access_guard: LiveFetchGuard | None = None,
        robots_allowed: bool | None = None,
        terms_reviewed: bool = False,
        manual_reviewed: bool = False,
        request_count: int = 1,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 20.0,
        user_agent: str | None = None,
    ) -> list[ConnectorDocument]:
        target_url = normalize_url(url or CDC_NOROSTAT_LIVE_URL)
        guard = access_guard or LiveFetchGuard(load_access_policies())
        guard.require_allowed(
            CDC_NOROSTAT_SOURCE_KEY,
            allow_network=allow_network,
            requested_url=target_url,
            robots_allowed=robots_allowed,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            request_count=request_count,
        )
        headers = {"User-Agent": user_agent or "infectious-intel-local/0.1"}
        with httpx.Client(
            follow_redirects=False,
            timeout=float(timeout_seconds),
            transport=transport,
            headers=headers,
        ) as client:
            response = client.get(target_url)
            self.last_live_response_summary = {
                "status_code": int(response.status_code),
                "content_type": response.headers.get("content-type"),
                "content_length": len(response.content or b""),
                "content_hash": hashlib.sha256(response.content or b"").hexdigest(),
                "final_url": str(response.url),
            }
            response.raise_for_status()
        documents = _parse_norostat_table(
            response.text,
            base_url=target_url,
            connector_name=self.connector_name,
            live_fetch=True,
        )
        if documents:
            return documents
        fallback = document_from_html(
            connector_name=self.connector_name,
            source_name="CDC NoroSTAT",
            url=target_url,
            html=response.text,
            metadata={
                "parse_mode": "html_summary",
                "live_fetch": True,
                "source_key": CDC_NOROSTAT_SOURCE_KEY,
            },
        )
        return [
            ConnectorDocument(
                connector_name=fallback.connector_name,
                source_name=fallback.source_name,
                url=fallback.url,
                title=fallback.title or "CDC NoroSTAT live page summary",
                body_text=fallback.body_text,
                content_type=fallback.content_type,
                metadata=fallback.metadata,
            )
        ]


class CdcNorsConnector:
    connector_name = "cdc_nors"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        data = json.loads(payload)
        documents: list[ConnectorDocument] = []
        for index, record in enumerate(data.get("outbreaks", []), start=1):
            title = text_from_record(record, ["state", "etiology", "year"])
            body = text_from_record(record, ["state", "setting", "etiology", "illnesses"])
            documents.append(
                ConnectorDocument(
                    connector_name=self.connector_name,
                    source_name="CDC NORS",
                    url=normalize_url(f"?outbreak={index}", base_url=base_url),
                    title=title or "CDC NORS outbreak record",
                    body_text=body,
                    content_type="application/json",
                    metadata={"record_number": index},
                )
            )
        return documents


class CdcFluViewConnector:
    connector_name = "cdc_fluview"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        csv_documents = _parse_fluview_generic(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            parser_strategy="csv_dataset",
        )
        if csv_documents:
            return csv_documents
        table_documents = _parse_fluview_generic(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            parser_strategy="html_table",
        )
        if table_documents:
            return table_documents
        return _parse_fluview_generic(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            parser_strategy="official_report_page",
        )


class CdcFluViewCsvConnector(CdcFluViewConnector):
    connector_name = "cdc_fluview_csv"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        return _parse_fluview_generic(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            parser_strategy="csv_dataset",
        )


class CdcFluViewHtmlTableConnector(CdcFluViewConnector):
    connector_name = "cdc_fluview_html_table"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        return _parse_fluview_generic(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            parser_strategy="html_table",
        )


class CdcFluViewReportConnector(CdcFluViewConnector):
    connector_name = "cdc_fluview_report"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        return _parse_fluview_generic(
            payload,
            base_url=base_url,
            connector_name=self.connector_name,
            parser_strategy="official_report_page",
        )


def _parse_fluview_generic(
    payload: str,
    *,
    base_url: str,
    connector_name: str,
    parser_strategy: str,
) -> list[ConnectorDocument]:
    result = parse_by_strategy(
        parser_strategy,
        payload,
        source_name="CDC FluView",
        url=base_url,
        country="United States",
        disease="influenza",
    )
    documents = parser_result_to_connector_documents(result)
    return [_with_fluview_metadata(document, connector_name=connector_name) for document in documents]


def _with_fluview_metadata(document: ConnectorDocument, *, connector_name: str) -> ConnectorDocument:
    metadata = {
        **document.metadata,
        "source_key": CDC_FLUVIEW_SOURCE_KEY,
        "source_type": document.metadata.get("source_type") or "official_dataset",
        "country_detected": "United States",
        "disease_detected": "influenza",
    }
    structured_event = metadata.get("structured_event")
    if isinstance(structured_event, dict):
        structured_event = {
            **structured_event,
            "source_key": CDC_FLUVIEW_SOURCE_KEY,
            "source_type": metadata["source_type"],
        }
        metadata["structured_event"] = structured_event
        if isinstance(metadata.get("structured_fields"), dict):
            metadata["structured_fields"] = {
                **metadata["structured_fields"],
                "source_key": CDC_FLUVIEW_SOURCE_KEY,
                "source_type": metadata["source_type"],
            }
    return ConnectorDocument(
        connector_name=connector_name,
        source_name="CDC FluView",
        url=document.url,
        title=document.title,
        body_text=document.body_text,
        content_type=document.content_type,
        metadata=metadata,
    )


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized == "table":
            self._current_table = []
        elif normalized == "tr" and self._current_table is not None:
            self._current_row = []
        elif normalized in {"td", "th"} and self._current_row is not None:
            self._cell_parts = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"td", "th"} and self._in_cell:
            value = _squash(" ".join(self._cell_parts or []))
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
        if self._in_cell and self._cell_parts is not None:
            self._cell_parts.append(data)


def _parse_norostat_table(
    html: str,
    *,
    base_url: str,
    connector_name: str,
    live_fetch: bool,
) -> list[ConnectorDocument]:
    parser = _TableParser()
    parser.feed(html or "")
    rows = _table_records(parser.tables)
    documents: list[ConnectorDocument] = []
    for index, record in enumerate(rows, start=1):
        state = _field(record, "state") or "Unknown jurisdiction"
        week = _field(record, "illness onset week") or _field(record, "week") or "unknown week"
        suspected = _field(record, "suspected outbreaks")
        confirmed = _field(record, "confirmed outbreaks")
        total = _field(record, "total outbreaks")
        title = f"CDC NoroSTAT norovirus outbreaks - {state} - {week}"
        structured_event = _structured_norostat_event(record)
        body = _squash(
            " ".join(
                part
                for part in (
                    "Official CDC NoroSTAT norovirus dataset row.",
                    f"State: {state}.",
                    f"Illness onset week: {week}.",
                    f"Suspected outbreaks: {suspected}." if suspected else "",
                    f"Confirmed outbreaks: {confirmed}." if confirmed else "",
                    f"Total outbreaks: {total}." if total else "",
                )
                if part
            )
        )
        documents.append(
            ConnectorDocument(
                connector_name=connector_name,
                source_name="CDC NoroSTAT",
                url=normalize_url(f"?norostat_row={index}", base_url=base_url),
                title=title,
                body_text=body,
                content_type="text/html",
                metadata={
                    "parse_mode": "html_table",
                    "live_fetch": live_fetch,
                    "source_key": CDC_NOROSTAT_SOURCE_KEY,
                    "source_type": "official_dataset",
                    "country_detected": "United States",
                    "disease_detected": "norovirus",
                    "structured_event": structured_event,
                    "row_number": index,
                    "row_fields": _safe_row_fields(record),
                },
            )
        )
    return documents


def _parse_fluview_csv(*args, **kwargs) -> list[ConnectorDocument]:
    payload = args[0] if args else ""
    base_url = kwargs["base_url"]
    connector_name = kwargs["connector_name"]
    try:
        reader = csv.DictReader(io.StringIO(payload or ""))
        if not reader.fieldnames:
            return []
        rows = [_normalize_record(row) for row in reader]
    except csv.Error:
        return []
    if not rows or not _looks_like_fluview_records(rows):
        return []
    return _fluview_documents_from_records(
        rows,
        base_url=base_url,
        connector_name=connector_name,
        parse_mode="csv_structured_data",
        content_type="text/csv",
    )


def _parse_fluview_html_table(html: str, *, base_url: str, connector_name: str) -> list[ConnectorDocument]:
    parser = _TableParser()
    parser.feed(html or "")
    rows = _fluview_table_records(parser.tables)
    if not rows:
        return []
    return _fluview_documents_from_records(
        rows,
        base_url=base_url,
        connector_name=connector_name,
        parse_mode="html_table",
        content_type="text/html",
    )


def _fluview_documents_from_records(
    rows: list[dict[str, str]],
    *,
    base_url: str,
    connector_name: str,
    parse_mode: str,
    content_type: str,
) -> list[ConnectorDocument]:
    documents: list[ConnectorDocument] = []
    for index, record in enumerate(rows, start=1):
        week = _first_field(record, "week", "iso week", "mmwr week", "week label")
        region = _first_field(record, "region", "state", "jurisdiction") or "United States"
        ili_percent = _parse_float(_first_field(record, "ili percent", "weighted ili", "% ili", "percent ili"))
        visits = _parse_int(_first_field(record, "ili visits", "visits", "ili cases", "cases"))
        tests = _parse_int(_first_field(record, "specimens tested", "tests", "total tests"))
        positive = _parse_int(_first_field(record, "positive specimens", "positive tests", "positives"))
        structured_event = _structured_fluview_event(record, week=week, region=region)
        body = _squash(
            " ".join(
                part
                for part in (
                    "Official CDC FluView influenza surveillance row.",
                    f"Region: {region}.",
                    f"Week: {week}.",
                    f"Influenza-like illness: {ili_percent}%." if ili_percent is not None else "",
                    f"ILI visits or cases: {visits}." if visits is not None else "",
                    f"Specimens tested: {tests}." if tests is not None else "",
                    f"Positive specimens: {positive}." if positive is not None else "",
                )
                if part
            )
        )
        documents.append(
            ConnectorDocument(
                connector_name=connector_name,
                source_name="CDC FluView",
                url=normalize_url(f"?fluview_row={index}&mode={parse_mode}", base_url=base_url),
                title=f"CDC FluView influenza surveillance - {region} - {week or 'unknown week'}",
                body_text=body,
                content_type=content_type,
                metadata={
                    "parse_mode": parse_mode,
                    "source_key": CDC_FLUVIEW_SOURCE_KEY,
                    "source_type": "official_dataset",
                    "country_detected": "United States",
                    "disease_detected": "influenza",
                    "structured_event": structured_event,
                    "row_number": index,
                    "row_fields": _safe_fluview_row_fields(record),
                },
            )
        )
    return documents


def _fluview_table_records(tables: list[list[list[str]]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    wanted = {"week", "iso week", "mmwr week", "region", "state", "ili percent", "weighted ili", "specimens tested"}
    for table in tables:
        if not table:
            continue
        headers = [_normalize_header(cell) for cell in table[0]]
        if not any(header in wanted for header in headers):
            continue
        for row in table[1:]:
            record = {
                headers[index]: value.strip()
                for index, value in enumerate(row)
                if index < len(headers) and headers[index]
            }
            if record:
                records.append(record)
    return records


def _structured_fluview_event(record: dict[str, str], *, week: str, region: str) -> dict[str, Any]:
    event_date, iso_week = _parse_explicit_week(week)
    cases = _parse_int(_first_field(record, "ili visits", "visits", "ili cases", "cases"))
    tests = _parse_int(_first_field(record, "specimens tested", "tests", "total tests"))
    positive = _parse_int(_first_field(record, "positive specimens", "positive tests", "positives"))
    positivity_rate = _parse_float(_first_field(record, "ili percent", "weighted ili", "% ili", "percent ili"))
    confirmed_cases = positive if positive is not None else cases
    return {
        "country": "United States",
        "admin1": None if region.lower() in {"national", "united states"} else region,
        "disease": "influenza",
        "event_date": event_date.isoformat() if event_date else None,
        "iso_week": iso_week,
        "week_label": week or None,
        "cases": cases,
        "confirmed_cases": confirmed_cases,
        "tests": tests,
        "positivity_rate": positivity_rate,
        "source_type": "official_dataset",
        "source_key": CDC_FLUVIEW_SOURCE_KEY,
    }


def _looks_like_fluview_records(rows: list[dict[str, str]]) -> bool:
    keys = {key for row in rows for key in row}
    return bool(keys & {"week", "iso week", "mmwr week"}) and bool(
        keys
        & {
            "ili percent",
            "weighted ili",
            "% ili",
            "ili visits",
            "visits",
            "specimens tested",
            "positive specimens",
            "cases",
        }
    )


def _normalize_record(record: dict[str, Any]) -> dict[str, str]:
    return {_normalize_header(key): str(value or "").strip() for key, value in record.items() if key is not None}


def _first_field(record: dict[str, str], *names: str) -> str:
    for name in names:
        value = record.get(name, "").strip()
        if value:
            return value
    return ""


def _safe_fluview_row_fields(record: dict[str, str]) -> dict[str, Any]:
    allowed = {
        "week",
        "iso week",
        "mmwr week",
        "week label",
        "region",
        "state",
        "jurisdiction",
        "ili percent",
        "weighted ili",
        "% ili",
        "ili visits",
        "visits",
        "ili cases",
        "cases",
        "specimens tested",
        "tests",
        "total tests",
        "positive specimens",
        "positive tests",
        "positives",
    }
    return {key: value for key, value in record.items() if key in allowed}


def _table_records(tables: list[list[list[str]]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    wanted = {"state", "week", "illness onset week", "suspected outbreaks", "confirmed outbreaks", "total outbreaks"}
    for table in tables:
        if not table:
            continue
        headers = [_normalize_header(cell) for cell in table[0]]
        if not any(header in wanted for header in headers):
            continue
        for row in table[1:]:
            record = {
                headers[index]: value.strip()
                for index, value in enumerate(row)
                if index < len(headers) and headers[index]
            }
            if record:
                records.append(record)
    return records


def _field(record: dict[str, str], name: str) -> str:
    return record.get(name, "").strip()


def _safe_row_fields(record: dict[str, str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key in {"state", "week", "illness onset week", "suspected outbreaks", "confirmed outbreaks", "total outbreaks"}
    }


def _structured_norostat_event(record: dict[str, str]) -> dict[str, Any]:
    state = _field(record, "state")
    week = _field(record, "illness onset week") or _field(record, "week")
    suspected = _parse_int(_field(record, "suspected outbreaks"))
    confirmed = _parse_int(_field(record, "confirmed outbreaks"))
    total = _parse_int(_field(record, "total outbreaks"))
    outbreaks_count = total
    if outbreaks_count is None and (suspected is not None or confirmed is not None):
        outbreaks_count = int(suspected or 0) + int(confirmed or 0)
    event_date, iso_week = _parse_explicit_week(week)
    return {
        "country": "United States",
        "admin1": state or None,
        "disease": "norovirus",
        "event_date": event_date.isoformat() if event_date else None,
        "iso_week": iso_week,
        "week_label": week or None,
        "suspected_outbreaks": suspected,
        "confirmed_outbreaks": confirmed,
        "outbreaks_count": outbreaks_count,
        "source_type": "official_dataset",
        "source_key": CDC_NOROSTAT_SOURCE_KEY,
    }


def _parse_explicit_week(value: str) -> tuple[date | None, str | None]:
    text = _squash(value)
    match = re.fullmatch(r"(\d{4})-W(\d{1,2})", text, flags=re.IGNORECASE)
    if not match:
        return None, None
    year, week = int(match.group(1)), int(match.group(2))
    try:
        parsed = date.fromisocalendar(year, week, 1)
    except ValueError:
        return None, None
    iso_year, iso_week, _ = parsed.isocalendar()
    return parsed, f"{iso_year}-W{iso_week:02d}"


def _parse_int(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _parse_float(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    return float(match.group(0)) if match else None


def _normalize_header(value: str) -> str:
    return _squash(value).lower()


def _squash(value: str) -> str:
    return " ".join(str(value or "").split())
