from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import httpx

from deepseek_web_search_agent.types import PageReadSummary, RankedSearchResult


class _SummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.meta_description: str | None = None
        self._tag: str | None = None
        self._title_chunks: list[str] = []
        self.headings: list[str] = []
        self.text_chunks: list[str] = []
        self.paragraph_chunks: list[str] = []
        self.table_count = 0
        self.table_headers: list[str] = []
        self.table_preview_rows: list[dict[str, str]] = []
        self._collect_table = False
        self._table_rows: list[list[tuple[str, str]]] = []
        self._current_row: list[tuple[str, str]] | None = None
        self._current_cell_tag: str | None = None
        self._current_cell_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._tag = tag
        attrs_map = {str(key).lower(): value for key, value in attrs}
        if tag == "meta" and str(attrs_map.get("name") or "").lower() == "description":
            content = " ".join(str(attrs_map.get("content") or "").split())
            if content:
                self.meta_description = content[:240]
        if tag == "table":
            self.table_count += 1
            if not self.table_headers and not self.table_preview_rows:
                self._collect_table = True
                self._table_rows = []
        elif self._collect_table and tag == "tr":
            self._current_row = []
        elif self._collect_table and tag in {"th", "td"}:
            self._current_cell_tag = tag
            self._current_cell_chunks = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            text = " ".join(self._title_chunks).strip()
            self.title = text or self.title
        elif self._collect_table and tag in {"th", "td"}:
            text = " ".join(" ".join(self._current_cell_chunks).split())[:120]
            if self._current_row is not None:
                self._current_row.append((self._current_cell_tag or tag, text))
            self._current_cell_tag = None
            self._current_cell_chunks = []
        elif self._collect_table and tag == "tr":
            if self._current_row:
                self._table_rows.append(self._current_row[:20])
            self._current_row = None
        elif self._collect_table and tag == "table":
            self._finalize_table_preview()
            self._collect_table = False
        self._tag = None

    def handle_data(self, data: str) -> None:
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self._current_cell_tag:
            self._current_cell_chunks.append(text)
        elif self._tag == "title":
            self._title_chunks.append(text)
        elif self._tag in {"h1", "h2"}:
            self.headings.append(text[:160])
        elif self._tag in {"p", "li", "td", "div"} and len(self.paragraph_chunks) < 120:
            self.paragraph_chunks.append(text[:260])
            if len(self.text_chunks) < 20:
                self.text_chunks.append(text[:220])
        elif len(self.text_chunks) < 20:
            self.text_chunks.append(text[:220])

    def summary(self) -> str:
        text = " ".join([self.meta_description or "", *self.headings[:6], *self.text_chunks[:10]])
        return text[:1000]

    def _finalize_table_preview(self) -> None:
        rows = [row for row in self._table_rows if any(cell for _, cell in row)]
        if not rows:
            return
        header_row_index = next((idx for idx, row in enumerate(rows) if any(tag == "th" for tag, _ in row)), 0)
        headers = [cell or f"column_{idx + 1}" for idx, (_, cell) in enumerate(rows[header_row_index][:20])]
        if not headers:
            return
        data_rows = rows[header_row_index + 1 : header_row_index + 6]
        preview: list[dict[str, str]] = []
        for row in data_rows:
            mapped: dict[str, str] = {}
            for idx, (_, cell) in enumerate(row[: len(headers)]):
                key = headers[idx] if idx < len(headers) else f"column_{idx + 1}"
                mapped[str(key)[:80] or f"column_{idx + 1}"] = str(cell or "")[:120]
            if any(mapped.values()):
                preview.append(mapped)
        self.table_headers = [str(item)[:80] for item in headers]
        self.table_preview_rows = preview[:5]


def read_pages_safely(
    ranked_results: list[RankedSearchResult],
    *,
    allow_page_read: bool = False,
    max_pages_to_read: int = 8,
    max_total_http_requests: int = 20,
    max_readable_pages_success: int = 8,
    stop_when_structured_candidates_found: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> tuple[list[PageReadSummary], dict[str, Any]]:
    if not allow_page_read:
        return [], {"page_read_request_count": 0, "status": "skipped_not_authorized"}
    summaries: list[PageReadSummary] = []
    request_count = 0
    readable_success = 0
    blocked_count = 0
    structured_candidates = 0
    for ranked in ranked_results:
        if request_count >= max_total_http_requests or request_count >= max_pages_to_read:
            break
        if readable_success >= max_readable_pages_success:
            break
        request_count += 1
        result = ranked.result
        if _looks_like_download(result.url):
            summaries.append(
                PageReadSummary(
                    url=result.url,
                    domain=result.domain,
                    title=result.title,
                    text_summary=None,
                    content_type=None,
                    request_count=0,
                    status="skipped_file_candidate",
                    hints={"file_candidate": True},
                )
            )
            continue
        try:
            with httpx.Client(timeout=12.0, follow_redirects=False, transport=transport) as client:
                response = client.get(result.url)
            content_type = response.headers.get("content-type")
            if response.status_code >= 400:
                if response.status_code in {403, 451}:
                    blocked_count += 1
                summaries.append(
                    PageReadSummary(
                        url=result.url,
                        domain=result.domain,
                        title=result.title,
                        text_summary=None,
                        content_type=content_type,
                        request_count=1,
                        status=f"http_{response.status_code}",
                    )
                )
                continue
            html_sample = response.text[:200_000]
            parser = _SummaryParser()
            parser.feed(html_sample)
            summary = parser.summary()
            hints = _structured_hints(
                html_sample,
                summary,
                content_type=content_type,
                table_count=parser.table_count,
                table_headers=parser.table_headers,
                table_preview_rows=parser.table_preview_rows,
                paragraph_chunks=parser.paragraph_chunks,
            )
            summaries.append(
                PageReadSummary(
                    url=result.url,
                    domain=result.domain,
                    title=parser.title or result.title,
                    text_summary=summary,
                    content_type=content_type,
                    request_count=1,
                    status="ok",
                    hints=hints,
                )
            )
            readable_success += 1
            if _has_structured_candidate(hints):
                structured_candidates += 1
                if stop_when_structured_candidates_found:
                    break
        except httpx.TimeoutException:
            summaries.append(
                PageReadSummary(
                    url=result.url,
                    domain=result.domain,
                    title=result.title,
                    text_summary=None,
                    content_type=None,
                    request_count=1,
                    status="timeout",
                )
            )
        except Exception:
            summaries.append(
                PageReadSummary(
                    url=result.url,
                    domain=result.domain,
                    title=result.title,
                    text_summary=None,
                    content_type=None,
                    request_count=1,
                    status="failed",
                )
            )
    return summaries, {
        "page_read_request_count": request_count,
        "readable_pages_success": readable_success,
        "blocked_pages_count": blocked_count,
        "structured_candidate_count": structured_candidates,
        "status": "ok",
    }


def _looks_like_download(url: str) -> bool:
    lowered = str(url or "").lower().split("?", 1)[0]
    return lowered.endswith((".pdf", ".zip", ".xlsx", ".xls", ".csv"))


def _has_structured_candidate(hints: dict[str, Any]) -> bool:
    has_preview = bool(
        hints.get("table_preview_rows_count")
        or hints.get("has_csv_hint")
        or hints.get("has_json_api_hint")
    )
    has_signal = int(hints.get("date_terms_count") or 0) > 0 and int(hints.get("numeric_terms_count") or 0) > 0
    return bool(has_preview and has_signal)


def _structured_hints(
    text: str,
    summary: str,
    *,
    content_type: str | None,
    table_count: int,
    table_headers: list[str],
    table_preview_rows: list[dict[str, str]],
    paragraph_chunks: list[str] | None = None,
) -> dict[str, Any]:
    combined = f"{summary} {' '.join(table_headers)} {' '.join(' '.join(row.values()) for row in table_preview_rows)}"
    lowered = str(text or "").lower()
    visible_lowered = combined.lower()
    paragraph_signal_preview = _paragraph_signal_preview(paragraph_chunks or [])
    date_terms = _matches(
        f"{combined} {' '.join(item.get('text', '') for item in paragraph_signal_preview)}",
        [
            r"\b20\d{2}-\d{1,2}-\d{1,2}\b",
            r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}\b",
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+20\d{2}\b",
            r"\b20\d{2}\s*[- ]?w(?:eek)?\s*\d{1,2}\b",
            r"\bweek\s+\d{1,2}\b",
            r"\bmmwr\s+week\b",
        ],
    )
    numeric_terms = _matches(
        f"{combined} {' '.join(item.get('text', '') for item in paragraph_signal_preview)}",
        [
            r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b",
            r"\bcase[s]?\b",
            r"\bdeath[s]?\b",
            r"\bsuspected\b",
            r"\bprobable\b",
            r"\bcontact[s]?\b",
            r"\bpositive\b",
            r"\bhospitalization[s]?\b",
        ],
    )
    has_csv = ".csv" in lowered or "text/csv" in str(content_type or "").lower()
    has_json = "application/json" in str(content_type or "").lower() or bool(re.search(r"\bapi\b|\.json\b", lowered, re.I))
    dated_numeric_paragraphs = [
        item for item in paragraph_signal_preview if item.get("has_date_or_week") and item.get("has_numeric_metric")
    ]
    case_paragraphs = [item for item in paragraph_signal_preview if item.get("case_signal")]
    death_paragraphs = [item for item in paragraph_signal_preview if item.get("death_signal")]
    contact_paragraphs = [item for item in paragraph_signal_preview if item.get("contact_signal")]
    situation_report_hint = bool(re.search(r"\bsituation report\b|\bepidemiological update\b|\bweekly bulletin\b", lowered, re.I))
    document_candidate = bool(re.search(r"\.pdf\b|pdf\b|download report|situation report", lowered, re.I))
    return {
        "table_count": int(table_count),
        "table_headers": table_headers[:20],
        "table_preview_rows": table_preview_rows[:5],
        "table_preview_rows_count": len(table_preview_rows[:5]),
        "paragraph_signal_preview": paragraph_signal_preview[:10],
        "paragraph_signal_preview_count": len(paragraph_signal_preview[:10]),
        "dated_numeric_paragraphs": dated_numeric_paragraphs[:10],
        "case_count_paragraphs": case_paragraphs[:10],
        "death_count_paragraphs": death_paragraphs[:10],
        "contact_monitoring_paragraphs": contact_paragraphs[:10],
        "dated_numeric_signal_count": len(dated_numeric_paragraphs),
        "rows_with_date_and_numeric_hint": len(dated_numeric_paragraphs) + len(table_preview_rows[:5]),
        "numeric_tokens_near_case_terms": sum(int(item.get("case_signal", False)) for item in paragraph_signal_preview),
        "numeric_tokens_near_death_terms": sum(int(item.get("death_signal", False)) for item in paragraph_signal_preview),
        "numeric_tokens_near_contact_terms": sum(int(item.get("contact_signal", False)) for item in paragraph_signal_preview),
        "table_cells_with_dates": _table_cells_with(table_preview_rows, _DATE_PATTERN),
        "table_cells_with_numeric_values": _table_cells_with(table_preview_rows, _NUMBER_PATTERN),
        "paragraphs_with_date_and_number": len(dated_numeric_paragraphs),
        "report_date_hint": bool(date_terms),
        "situation_report_hint": situation_report_hint,
        "document_download_hint": document_candidate,
        "data_table_hint": table_count > 0 or bool(table_preview_rows),
        "extraction_ready_hint": bool(dated_numeric_paragraphs or (table_preview_rows and date_terms and numeric_terms)),
        "extraction_ready_blocker": None
        if (dated_numeric_paragraphs or (table_preview_rows and date_terms and numeric_terms))
        else _extraction_ready_blocker(date_terms, numeric_terms, table_preview_rows, paragraph_signal_preview),
        "date_terms_count": len(date_terms),
        "week_terms_count": len(_matches(combined, [r"\bweek\b", r"\bmmwr\b", r"\b20\d{2}-w\d{1,2}\b"])),
        "numeric_terms_count": len(numeric_terms),
        "cases_terms_count": len(_matches(visible_lowered, [r"\bcase[s]?\b", r"\bpositive\b"])),
        "deaths_terms_count": len(_matches(visible_lowered, [r"\bdeath[s]?\b"])),
        "suspected_terms_count": len(_matches(visible_lowered, [r"\bsuspected\b"])),
        "contacts_terms_count": len(_matches(visible_lowered, [r"\bcontact[s]?\b"])),
        "surveillance_terms_count": len(_matches(visible_lowered, [r"\bsurveillance\b", r"\bfluview\b", r"\bili\b"])),
        "date_or_week_terms": sorted(set(date_terms))[:20],
        "numeric_terms": sorted(set(numeric_terms))[:20],
        "has_table_hint": table_count > 0 or "<table" in lowered,
        "has_csv_hint": has_csv,
        "has_json_api_hint": has_json,
        "has_download_hint": bool(re.search(r"\.(csv|xlsx|xls|json|api)\b", lowered, re.I)),
        "has_dashboard_hint": bool(re.search(r"\bdashboard\b|\binteractive\b|\btableau\b|\bdata portal\b", lowered, re.I)),
        "raw_html_saved": False,
    }


def _matches(text: str, patterns: list[str]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(match.group(0)[:80] for match in re.finditer(pattern, str(text or ""), flags=re.I))
    return matches[:50]


_DATE_PATTERN = re.compile(
    r"\b20\d{2}-\d{1,2}-\d{1,2}\b|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+20\d{2}\b|\bweek\s+\d{1,2}\b|\b20\d{2}-?w\d{1,2}\b",
    re.I,
)
_NUMBER_PATTERN = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b")
_CASE_PATTERN = re.compile(r"\bcase[s]?\b|\bconfirmed\b|\bsuspected\b|\bprobable\b|\bpositive\b", re.I)
_DEATH_PATTERN = re.compile(r"\bdeath[s]?\b|\bfatalit(?:y|ies)\b", re.I)
_CONTACT_PATTERN = re.compile(r"\bcontact[s]?\b|\bmonitoring\b|\bfollow[- ]?up\b", re.I)


def _paragraph_signal_preview(paragraphs: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        text = " ".join(str(paragraph or "").split())
        if not text:
            continue
        has_date = bool(_DATE_PATTERN.search(text))
        has_number = bool(_NUMBER_PATTERN.search(text))
        has_case = bool(_CASE_PATTERN.search(text) and has_number)
        has_death = bool(_DEATH_PATTERN.search(text) and has_number)
        has_contact = bool(_CONTACT_PATTERN.search(text) and has_number)
        has_metric = has_number and (has_case or has_death or has_contact)
        if has_date and has_number or has_metric:
            rows.append(
                {
                    "text": text[:200],
                    "has_date_or_week": has_date,
                    "has_numeric_metric": has_metric,
                    "case_signal": has_case,
                    "death_signal": has_death,
                    "contact_signal": has_contact,
                }
            )
        if len(rows) >= 10:
            break
    return rows


def _table_cells_with(rows: list[dict[str, str]], pattern: re.Pattern[str]) -> int:
    count = 0
    for row in rows[:5]:
        for value in row.values():
            if pattern.search(str(value or "")):
                count += 1
    return count


def _extraction_ready_blocker(
    date_terms: list[str],
    numeric_terms: list[str],
    table_preview_rows: list[dict[str, str]],
    paragraph_signal_preview: list[dict[str, Any]],
) -> str:
    if not table_preview_rows and not paragraph_signal_preview:
        return "no_table_or_paragraph_signal_preview"
    if not date_terms and numeric_terms:
        return "numeric_signal_without_date"
    if date_terms and not numeric_terms:
        return "date_signal_without_numeric"
    return "needs_structured_document_or_dataset"
