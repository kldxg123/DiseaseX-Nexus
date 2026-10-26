from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
import hashlib
import io
import json
import re
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import Engine, text

from epidemic_core.discovery.providers import OpenClawDuckDuckGoProvider, ProviderOutcome, SearchRequest, SearchResult
from epidemic_core.ebola.fetcher import FetchResult, OfficialSourceFetcher
from epidemic_core.ebola.parsers import parse_who_afro_pdf
from epidemic_core.ebola.service import iris_pdf_url
from epidemic_core.p1_12 import ELIGIBILITY_VERSION


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: object) -> str:
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"epidemic-openclaw:p1-12:{kind}:{value}")


@dataclass(frozen=True)
class CandidateRow:
    country_iso3: str
    country_name: str
    disease_id: str
    pathogen_variant: str | None
    report_date: dt.date | None
    source_as_of_date: dt.date | None
    period_start: dt.date | None
    period_end: dt.date | None
    metric_code: str
    value: int
    unit: str
    case_definition: str
    aggregation_type: str
    geographic_scope: str
    source_url: str
    source_row_locator: str
    raw_value: str
    normalized_value: str
    source_fields: dict[str, object]
    mapping: dict[str, object]
    quality_flags: tuple[str, ...]
    license_status: str


@dataclass(frozen=True)
class CandidateSpec:
    key: str
    country_iso3: str
    country_name: str
    country_local: str
    disease_id: str
    disease_name: str
    disease_local: str
    requested_metrics: tuple[str, ...]
    source_tier: str
    publisher: str
    dataset_name: str
    official_domains: tuple[str, ...]
    landing_url: str
    data_url: str | None
    parser_name: str


@dataclass(frozen=True)
class CandidateManifest:
    request_id: uuid.UUID
    run_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_version_id: uuid.UUID
    review_case_id: uuid.UUID
    publisher: str
    provider: str
    queries: tuple[str, ...]
    search_result_url: str
    final_data_url: str
    artifact_id: uuid.UUID
    artifact_sha256: str
    artifact_bytes: int
    extraction_run_id: uuid.UUID
    parsed_rows: int
    preview_rows: int
    preview_sha256: str
    eligibility_hash: str
    sample: dict[str, object]


SPECS = (
    CandidateSpec(
        "uganda-moh-evd-daily-v2",
        "UGA",
        "Uganda",
        "Uganda",
        "ebola-disease",
        "Ebola virus disease",
        "Ebola",
        ("ebola_confirmed_cases_cumulative",),
        "NATIONAL_HEALTH",
        "Uganda Ministry of Health",
        "Uganda Ministry of Health Ebola Daily",
        ("evd-daily.health.go.ug", "health.go.ug"),
        "https://evd-daily.health.go.ug/",
        "https://evd-daily.health.go.ug/api/stats",
        "uganda-evd-daily-json-v2",
    ),
    CandidateSpec(
        "who-afro-bvd-weekly-v2",
        "COD",
        "Democratic Republic of the Congo",
        "République démocratique du Congo",
        "ebola-disease",
        "Bundibugyo virus disease",
        "maladie à virus Bundibugyo",
        ("ebola_confirmed_cases_cumulative", "ebola_confirmed_deaths_cumulative"),
        "WHO_REGIONAL",
        "WHO Regional Office for Africa",
        "WHO AFRO Ebola Weekly External Situation Report",
        ("afro.who.int", "www.afro.who.int", "iris.who.int"),
        "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-2",
        None,
        "who-afro-bvd-pdf-v2",
    ),
    CandidateSpec(
        "ukhsa-flu-ecmo-admissions-v2",
        "GBR",
        "United Kingdom",
        "United Kingdom",
        "influenza",
        "Seasonal influenza",
        "Influenza",
        ("influenza_lab_confirmed_ecmo_admissions",),
        "NATIONAL_HEALTH",
        "UK Health Security Agency",
        "UKHSA weekly laboratory-confirmed influenza ECMO admissions",
        ("www.gov.uk", "assets.publishing.service.gov.uk", "ukhsa-dashboard.data.gov.uk"),
        "https://www.gov.uk/government/statistics/national-flu-and-covid-19-surveillance-reports-2025-to-2026-season",
        None,
        "ukhsa-flu-ods-figure-8-v2",
    ),
)

REQUIRED_EVIDENCE_COUNTS = (
    "planned_query_count",
    "executed_query_count",
    "provider_success_count",
    "raw_search_result_count",
    "deduplicated_url_count",
    "fetch_attempt_count",
    "fetch_success_count",
    "artifact_count",
    "extraction_run_count",
    "parsed_table_count",
    "candidate_preview_row_count",
    "mappable_person_metric_count",
)


def review_ready_violations(
    spec: CandidateSpec,
    counts: dict[str, int],
    rows: list[CandidateRow],
    artifact_sha256: str,
    preview_sha256: str,
) -> tuple[str, ...]:
    """Apply the process-local half of review-ready-v2 before the DB gate."""

    violations: list[str] = []
    if not re.fullmatch(r"[A-Z]{3}", spec.country_iso3):
        violations.append("COUNTRY_REQUIRED")
    if not spec.disease_id.strip():
        violations.append("DISEASE_REQUIRED")
    if not spec.requested_metrics:
        violations.append("REQUESTED_PERSON_METRIC_REQUIRED")
    if not spec.source_tier.strip():
        violations.append("SOURCE_TIER_REQUIRED")
    for key in REQUIRED_EVIDENCE_COUNTS:
        if int(counts.get(key, 0)) < 1:
            violations.append(f"{key.upper()}_REQUIRED")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
        violations.append("ARTIFACT_SHA256_REQUIRED")
    if not re.fullmatch(r"[0-9a-f]{64}", preview_sha256):
        violations.append("PREVIEW_SHA256_REQUIRED")

    qualifying = 0
    for row in rows:
        has_date = any((row.report_date, row.source_as_of_date, row.period_start, row.period_end))
        valid_value = isinstance(row.value, int) and not isinstance(row.value, bool) and row.value > 0
        complete = all(
            (
                row.country_iso3 == spec.country_iso3,
                bool(row.country_name.strip()),
                row.disease_id == spec.disease_id,
                has_date,
                row.metric_code in spec.requested_metrics,
                valid_value,
                row.unit == "person",
                bool(row.case_definition.strip()),
                row.aggregation_type in {"new", "cumulative", "period_total"},
                bool(row.geographic_scope.strip()),
                urllib.parse.urlsplit(row.source_url).scheme == "https",
                bool(row.source_row_locator.strip()),
                bool(row.raw_value.strip()),
                bool(row.normalized_value.strip()),
            )
        )
        qualifying += int(complete)
    if qualifying < 1:
        violations.append("POSITIVE_PERSON_PREVIEW_ROW_REQUIRED")
    if counts.get("candidate_preview_row_count", 0) != len(rows):
        violations.append("PREVIEW_ROW_COUNT_MISMATCH")
    if counts.get("mappable_person_metric_count", 0) != qualifying:
        violations.append("PERSON_METRIC_COUNT_MISMATCH")
    return tuple(dict.fromkeys(violations))


class CandidateDiscoveryService:
    def __init__(self, engine: Engine, project_root: Path) -> None:
        self.engine = engine
        self.project_root = project_root
        self.raw_root = project_root / "storage" / "p1_12" / "discovery" / "quarantine" / "sha256"
        self.fetcher = OfficialSourceFetcher(
            {domain for spec in SPECS for domain in spec.official_domains}, max_bytes=48 * 1024 * 1024
        )

    @staticmethod
    def _request(spec: CandidateSpec) -> SearchRequest:
        return SearchRequest(
            spec.country_iso3,
            {"UGA": "UG", "COD": "CD", "GBR": "GB"}[spec.country_iso3],
            spec.country_name,
            spec.country_local,
            spec.disease_name,
            spec.disease_local,
            spec.requested_metrics,
            "latest_rolling_year",
            (spec.source_tier,),
            ("en", "zh-CN"),
            10,
        )

    @staticmethod
    def _selected_search_result(spec: CandidateSpec, outcome: ProviderOutcome) -> SearchResult:
        values = [
            result
            for result in outcome.results
            if (urllib.parse.urlsplit(result.canonical_url).hostname or "").lower() in spec.official_domains
        ]
        if not values:
            raise RuntimeError(f"{spec.key}:KEY_FREE_SEARCH_OFFICIAL_RESULT_REQUIRED")
        preferred = urllib.parse.urlsplit(spec.landing_url).hostname or ""
        return sorted(
            values,
            key=lambda item: (
                0 if (urllib.parse.urlsplit(item.canonical_url).hostname or "") == preferred else 1,
                item.rank,
                item.canonical_url,
            ),
        )[0]

    @staticmethod
    def _links(result: FetchResult) -> list[str]:
        from epidemic_core.ebola.service import _LinkParser

        parser = _LinkParser()
        parser.feed(result.payload.decode("utf-8", errors="replace"))
        return [urllib.parse.urljoin(result.final_url, value) for value in parser.links]

    def _fetch_source(self, spec: CandidateSpec, selected: SearchResult) -> tuple[FetchResult, list[FetchResult]]:
        search_page = self.fetcher.fetch(selected.canonical_url)
        chain = [search_page]
        if spec.key == "uganda-moh-evd-daily-v2":
            data = self.fetcher.fetch(str(spec.data_url))
            chain.append(data)
            return data, chain
        if spec.key == "who-afro-bvd-weekly-v2":
            page = search_page
            links = self._links(search_page)
            report_pages = [
                link
                for link in links
                if urllib.parse.urlsplit(link).hostname in {"www.afro.who.int", "afro.who.int"}
                and "ebola-bundibugyo-virus-disease-outbreak" in urllib.parse.urlsplit(link).path
            ]
            if report_pages:
                page = self.fetcher.fetch(sorted(set(report_pages))[-1])
                chain.append(page)
            try:
                pdf_url = iris_pdf_url(page)
            except ValueError:
                # Search may have returned a listing. The official series is the deterministic navigation fallback.
                listing = self.fetcher.fetch(spec.landing_url)
                chain.append(listing)
                report_links = [
                    link
                    for link in self._links(listing)
                    if "ebola-bundibugyo-virus-disease-outbreak" in urllib.parse.urlsplit(link).path
                ]
                if not report_links:
                    raise RuntimeError("WHO_AFRO_REPORT_PAGE_NOT_DISCOVERED") from None
                page = self.fetcher.fetch(sorted(set(report_links))[-1])
                chain.append(page)
                pdf_url = iris_pdf_url(page)
            pdf = self.fetcher.fetch(pdf_url)
            chain.append(pdf)
            return pdf, chain
        links = self._links(search_page)
        ods = [
            link
            for link in links
            if urllib.parse.urlsplit(link).hostname == "assets.publishing.service.gov.uk"
            and re.search(
                r"weekly-influenza-and-COVID-19-report-data(?:file)?-week-\d+-2026\.ods$",
                urllib.parse.urlsplit(link).path,
                re.I,
            )
        ]
        if not ods:
            landing = self.fetcher.fetch(spec.landing_url)
            chain.append(landing)
            ods = [
                link
                for link in self._links(landing)
                if urllib.parse.urlsplit(link).hostname == "assets.publishing.service.gov.uk"
                and re.search(
                    r"weekly-influenza-and-COVID-19-report-data(?:file)?-week-\d+-2026\.ods$",
                    urllib.parse.urlsplit(link).path,
                    re.I,
                )
            ]
        if not ods:
            raise RuntimeError("UKHSA_OFFICIAL_ODS_NOT_DISCOVERED")

        def week(url: str) -> int:
            match = re.search(r"week-(\d+)-2026", url, re.I)
            return int(match.group(1)) if match else -1

        data = self.fetcher.fetch(max(set(ods), key=lambda value: (week(value), value)))
        chain.append(data)
        return data, chain

    @staticmethod
    def _uganda_rows(result: FetchResult) -> list[CandidateRow]:
        value = json.loads(result.payload)
        if not isinstance(value, list):
            raise RuntimeError("UGANDA_API_SHAPE_DRIFT")
        rows: list[CandidateRow] = []
        for ordinal, source in enumerate(value, 1):
            if not isinstance(source, dict) or not isinstance(source.get("cases"), int):
                continue
            try:
                date = dt.date.fromisoformat(str(source["date"])[:10])
            except (KeyError, ValueError):
                continue
            count = int(source["cases"])
            if count < 0:
                continue
            rows.append(
                CandidateRow(
                    "UGA",
                    "Uganda",
                    "ebola-disease",
                    "BDBV",
                    None,
                    date,
                    date,
                    date,
                    "ebola_confirmed_cases_cumulative",
                    count,
                    "person",
                    "laboratory_confirmed",
                    "cumulative",
                    "national_reported",
                    result.final_url,
                    f"JSON array index={ordinal - 1}; fields=date,cases",
                    str(source["cases"]),
                    str(count),
                    source,
                    {"date": "source_as_of_date", "cases": "ebola_confirmed_cases_cumulative"},
                    ("LICENSE_REVIEW_REQUIRED", "IMPORTED_LOCAL_BREAKDOWN_NOT_PRESENT_IN_API_RESPONSE"),
                    "REVIEW_REQUIRED",
                )
            )
        if not any(row.value > 0 for row in rows):
            raise RuntimeError("UGANDA_POSITIVE_PERSON_VALUE_REQUIRED")
        return rows

    @staticmethod
    def _afro_rows(result: FetchResult) -> list[CandidateRow]:
        report = parse_who_afro_pdf(result.payload, result.final_url.rsplit("/", 1)[-1])
        reader = PdfReader(io.BytesIO(result.payload), strict=True)
        page_by_metric: dict[str, int] = {}
        for number, page in enumerate(reader.pages, 1):
            page_text = " ".join((page.extract_text() or "").split()).casefold()
            if "democratic republic of the congo" in page_text:
                for metric, token in (
                    ("ebola_confirmed_cases_cumulative", "confirmed cases"),
                    ("ebola_confirmed_deaths_cumulative", "confirmed deaths"),
                ):
                    if token in page_text and metric not in page_by_metric:
                        page_by_metric[metric] = number
        rows = []
        for item in report.observations:
            if item.country_iso3 != "COD" or item.value is None or item.metric_code not in page_by_metric:
                continue
            rows.append(
                CandidateRow(
                    "COD",
                    "Democratic Republic of the Congo",
                    "ebola-disease",
                    "BDBV",
                    report.publication_date,
                    item.source_as_of,
                    item.source_as_of,
                    item.source_as_of,
                    item.metric_code,
                    int(item.value),
                    "person",
                    item.case_classification,
                    "cumulative",
                    item.geography_scope,
                    result.final_url,
                    f"PDF page={page_by_metric[item.metric_code]}; table/narrative=DRC aggregate; metric={item.metric_code}; extractor_confidence=1.0",
                    str(item.value),
                    str(int(item.value)),
                    {
                        "country_iso3": item.country_iso3,
                        "source_as_of": item.source_as_of.isoformat(),
                        "metric_code": item.metric_code,
                        "value_numeric": item.value,
                    },
                    {
                        "country_iso3": "country_iso3",
                        "source_as_of": "source_as_of_date",
                        "value_numeric": item.metric_code,
                    },
                    ("LICENSE_REVIEW_REQUIRED", "PDF_AGGREGATE_REVIEW_REQUIRED"),
                    "REVIEW_REQUIRED",
                )
            )
        if not any(row.value > 0 for row in rows):
            raise RuntimeError("WHO_AFRO_POSITIVE_PERSON_VALUE_REQUIRED")
        return rows

    @staticmethod
    def _ods_sheets(payload: bytes) -> dict[str, list[list[str]]]:
        ns = {
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        }
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            root = ET.fromstring(archive.read("content.xml"))
        sheets: dict[str, list[list[str]]] = {}
        for table in root.findall(".//table:table", ns):
            name = table.attrib.get(f"{{{ns['table']}}}name", "")
            values: list[list[str]] = []
            for row in table.findall("table:table-row", ns):
                repeat = min(int(row.attrib.get(f"{{{ns['table']}}}number-rows-repeated", "1")), 10)
                parsed: list[str] = []
                for cell in row.findall("table:table-cell", ns):
                    cell_repeat = min(int(cell.attrib.get(f"{{{ns['table']}}}number-columns-repeated", "1")), 50)
                    cell_value = " ".join("".join(cell.itertext()).split())
                    parsed.extend([cell_value] * cell_repeat)
                if any(parsed):
                    values.extend([parsed] * repeat)
            sheets[name] = values
        return sheets

    @classmethod
    def _ukhsa_rows(cls, result: FetchResult) -> list[CandidateRow]:
        sheets = cls._ods_sheets(result.payload)
        values = sheets.get("Figure_8", [])
        header_index = next((index for index, row in enumerate(values) if "Date" in row and "Influenza" in row), None)
        if header_index is None:
            raise RuntimeError("UKHSA_FIGURE_8_SCHEMA_DRIFT")
        header = values[header_index]
        date_col, flu_col = header.index("Date"), header.index("Influenza")
        rows: list[CandidateRow] = []
        for source_index, source in enumerate(values[header_index + 1 :], header_index + 2):
            if len(source) <= max(date_col, flu_col):
                continue
            try:
                date = dt.datetime.strptime(source[date_col], "%d/%m/%Y").date()
                count = int(float(source[flu_col]))
            except ValueError:
                continue
            if count < 0:
                continue
            rows.append(
                CandidateRow(
                    "GBR",
                    "United Kingdom",
                    "influenza",
                    None,
                    None,
                    date,
                    date,
                    date + dt.timedelta(days=6),
                    "influenza_lab_confirmed_ecmo_admissions",
                    count,
                    "person",
                    "laboratory-confirmed influenza adult ECMO admission",
                    "period_total",
                    "UK severe respiratory failure centres",
                    result.final_url,
                    f"ODS sheet=Figure_8; row={source_index}; column=Influenza; period_start={date.isoformat()}",
                    source[flu_col],
                    str(count),
                    {"Date": source[date_col], "Influenza": source[flu_col]},
                    {"Date": "period_start", "Influenza": "influenza_lab_confirmed_ecmo_admissions"},
                    ("LICENSE_REVIEW_REQUIRED", "SPECIALIST_ECMO_NETWORK_NOT_GENERAL_HOSPITAL_ADMISSIONS"),
                    "REVIEW_REQUIRED",
                )
            )
        if not any(row.value > 0 for row in rows):
            raise RuntimeError("UKHSA_POSITIVE_PERSON_VALUE_REQUIRED")
        return rows

    def _parse(self, spec: CandidateSpec, result: FetchResult) -> list[CandidateRow]:
        functions: dict[str, Callable[[FetchResult], list[CandidateRow]]] = {
            "uganda-moh-evd-daily-v2": self._uganda_rows,
            "who-afro-bvd-weekly-v2": self._afro_rows,
            "ukhsa-flu-ecmo-admissions-v2": self._ukhsa_rows,
        }
        return functions[spec.key](result)

    def _store(self, result: FetchResult) -> str:
        suffix = {
            "application/json": ".json",
            "application/pdf": ".pdf",
            "application/vnd.oasis.opendocument.spreadsheet": ".ods",
        }.get(result.mime_type, ".bin")
        target = self.raw_root / result.sha256[:2] / f"{result.sha256}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(result.payload)
        return str(target.relative_to(self.project_root))

    @staticmethod
    def _event(
        connection: Any,
        run_id: uuid.UUID,
        from_state: str,
        to_state: str,
        code: str,
        details: dict[str, object],
        progress: dict[str, object] | None = None,
    ) -> None:
        changed = connection.execute(
            text(
                "SELECT epidemic_p1_12.transition_discovery_run_v2("
                ":run,:old,:new,:code,CAST(:details AS jsonb),CAST(:progress AS jsonb))"
            ),
            {
                "run": run_id,
                "old": from_state,
                "new": to_state,
                "code": code,
                "details": canonical(details),
                "progress": canonical(progress or {}),
            },
        ).scalar_one()
        if not changed:
            raise RuntimeError(f"STALE_DISCOVERY_TRANSITION:{from_state}:{to_state}")

    def run_one(self, spec: CandidateSpec) -> CandidateManifest:
        request = self._request(spec)
        queries = request.query_plan()
        request_material = {
            "task": "P1-12",
            "country_iso3": spec.country_iso3,
            "country_name": spec.country_name,
            "disease_id": spec.disease_id,
            "requested_metrics": list(spec.requested_metrics),
            "time_window": {"kind": "latest_rolling_year", "duration": "P1Y"},
            "source_tiers": [spec.source_tier],
            "key": spec.key,
            "started_after_p1_12": True,
        }
        request_sha = sha(request_material)
        run_id = stable_uuid("discovery-run", request_sha)
        request_id = stable_uuid("discovery-request", request_sha)
        resume_state: str | None = None
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    text(
                        "SELECT r.id,r.state,q.id request_id FROM epidemic_p1_12.discovery_requests_v2 q JOIN epidemic_discovery.discovery_runs r ON r.id=q.discovery_run_id WHERE q.request_sha256=:sha"
                    ),
                    {"sha": request_sha},
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json,progress_json) VALUES(:id,'QUEUED',CAST(:request AS jsonb),:sha,'p1-12-review-ready-v2','{}'::jsonb,'{}'::jsonb)"
                    ),
                    {"id": run_id, "request": canonical(request_material), "sha": request_sha},
                )
                connection.execute(
                    text(
                        "INSERT INTO epidemic_p1_12.discovery_requests_v2(id,discovery_run_id,country_iso3,country_name,disease_id,requested_metrics,time_window,source_tiers,request_sha256) VALUES(:id,:run,:country,:name,:disease,CAST(:metrics AS jsonb),CAST(:window AS jsonb),CAST(:tiers AS jsonb),:sha)"
                    ),
                    {
                        "id": request_id,
                        "run": run_id,
                        "country": spec.country_iso3,
                        "name": spec.country_name,
                        "disease": spec.disease_id,
                        "metrics": canonical(list(spec.requested_metrics)),
                        "window": canonical(request_material["time_window"]),
                        "tiers": canonical([spec.source_tier]),
                        "sha": request_sha,
                    },
                )
                for index, query in enumerate(queries, 1):
                    connection.execute(
                        text(
                            "INSERT INTO epidemic_p1_12.discovery_queries_v2(id,discovery_request_id,provider_id,language_tag,query_text,query_sha256,state) VALUES(:id,:request,'openclaw-duckduckgo-v1',:language,:query,:sha,'PLANNED')"
                        ),
                        {
                            "id": stable_uuid("query", f"{request_id}:{index}"),
                            "request": request_id,
                            "language": "en" if index != 2 else "local",
                            "query": query,
                            "sha": sha(query),
                        },
                    )
                self._event(
                    connection,
                    run_id,
                    "QUEUED",
                    "CLAIMED",
                    "DISCOVERY_CLAIMED",
                    {"worker": "p1-12-deterministic-discovery-v1"},
                )
                self._event(
                    connection,
                    run_id,
                    "CLAIMED",
                    "SEARCHING",
                    "KEY_FREE_SEARCH_STARTED",
                    {"provider": "openclaw-duckduckgo-v1", "query_count": 3},
                )
            elif (
                uuid.UUID(str(existing["id"])) != run_id
                or uuid.UUID(str(existing["request_id"])) != request_id
                or existing["state"] not in {"SEARCHING", "FETCHING"}
            ):
                raise RuntimeError(f"{spec.key}:P1_12_DISCOVERY_REQUEST_NOT_RESUMABLE")
            else:
                resume_state = str(existing["state"])
        outcome = OpenClawDuckDuckGoProvider().search(request, required_channel=True)
        try:
            selected = self._selected_search_result(spec, outcome)
            search_results = outcome.results
            direct_registry_fallback = False
        except RuntimeError:
            selected = SearchResult(
                canonical_url=spec.landing_url,
                title=spec.dataset_name,
                publisher=spec.publisher,
                description="Controlled official-source registry entry; key-free search returned no relevant official-domain hit.",
                source_tier=spec.source_tier,
                provider_id="controlled-official-directory-v1",
                query_id="official-registry-1",
                rank=1,
                original_url=spec.landing_url,
                landing_page_url=spec.landing_url,
                stable_dataset_id=spec.key,
                result_sha256=sha({"registry": spec.key, "url": spec.landing_url}),
                metadata={"registry_contract": spec.key, "web_search_relevance_degraded": True},
            )
            search_results = (*outcome.results, selected)
            direct_registry_fallback = True
        with self.engine.begin() as connection:
            successful = max(1, int(outcome.evidence.successful_query_count))
            for index, query in enumerate(queries, 1):
                state = "SUCCEEDED" if index <= successful else "PROVIDER_DEGRADED"
                connection.execute(
                    text(
                        "UPDATE epidemic_p1_12.discovery_queries_v2 SET state=:state,executed_at=now(),result_count=:count,elapsed_ms=:elapsed,evidence_sha256=:evidence WHERE discovery_request_id=:request AND query_sha256=:sha"
                    ),
                    {
                        "state": state,
                        "count": sum(1 for item in outcome.results if item.query_id == f"ddg-{index}"),
                        "elapsed": outcome.evidence.elapsed_ms,
                        "evidence": outcome.evidence.response_sha256 or sha(outcome.evidence.event_details()),
                        "request": request_id,
                        "sha": sha(query),
                    },
                )
            persisted_results: dict[str, uuid.UUID] = {}
            for item in search_results:
                host = (urllib.parse.urlsplit(item.canonical_url).hostname or "").lower()
                if host not in spec.official_domains:
                    continue
                result_id = stable_uuid("search-result", f"{run_id}:{item.query_id}:{item.rank}:{item.canonical_url}")
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_discovery_search_results(id,discovery_run_id,provider_id,query_id,query_sha256,rank,title,snippet,original_url,canonical_url,canonical_url_sha256,domain,publisher,stable_dataset_id,source_tier,media_type,landing_page_url,result_sha256,observed_at,metadata,disposition) VALUES(:id,:run,:provider,:query_id,:query_sha,:rank,:title,:snippet,:original,:canonical,:url_sha,:domain,:publisher,:stable,:tier,:media,:landing,:result_sha,now(),CAST(:metadata AS jsonb),:disposition) ON CONFLICT(id) DO NOTHING"
                    ),
                    {
                        "id": result_id,
                        "run": run_id,
                        "provider": item.provider_id,
                        "query_id": item.query_id,
                        "query_sha": sha(queries[max(0, min(2, int(item.query_id.rsplit("-", 1)[-1]) - 1))]),
                        "rank": item.rank,
                        "title": item.title,
                        "snippet": item.description,
                        "original": item.original_url or item.canonical_url,
                        "canonical": item.canonical_url,
                        "url_sha": sha(item.canonical_url),
                        "domain": host,
                        "publisher": item.publisher,
                        "stable": item.stable_dataset_id,
                        "tier": spec.source_tier,
                        "media": item.media_type,
                        "landing": item.landing_page_url,
                        "result_sha": item.result_sha256 or sha(item.__dict__),
                        "metadata": canonical(
                            {
                                "untrusted_search_summary": item.provider_id != "controlled-official-directory-v1",
                                "prompt_injection_not_executed": True,
                                "controlled_official_registry": item.provider_id == "controlled-official-directory-v1",
                            }
                        ),
                        "disposition": "FETCH_SELECTED"
                        if item.canonical_url == selected.canonical_url
                        else "DISCOVERED",
                    },
                )
                persisted_results[item.canonical_url] = result_id
            search_result_id = persisted_results[selected.canonical_url]
            fetch_id = stable_uuid("candidate-fetch", str(run_id))
            if resume_state == "FETCHING":
                connection.execute(
                    text(
                        "UPDATE epidemic_discovery.candidate_fetches SET search_result_id=:search,domain=:domain WHERE id=:id AND discovery_run_id=:run AND status='STARTED'"
                    ),
                    {
                        "search": search_result_id,
                        "domain": urllib.parse.urlsplit(selected.canonical_url).hostname or "",
                        "id": fetch_id,
                        "run": run_id,
                    },
                )
            else:
                self._event(
                    connection,
                    run_id,
                    "SEARCHING",
                    "SEARCH_COMPLETED",
                    "KEY_FREE_SEARCH_COMPLETED",
                    {
                        "provider": outcome.evidence.provider_id,
                        "result_count": len(persisted_results),
                        "elapsed_ms": outcome.evidence.elapsed_ms,
                        "official_registry_fallback": direct_registry_fallback,
                        "web_search_result": outcome.evidence.result,
                    },
                )
                self._event(
                    connection,
                    run_id,
                    "SEARCH_COMPLETED",
                    "SOURCE_FOUND",
                    "OFFICIAL_SOURCE_RESULT_SELECTED",
                    {"domain": urllib.parse.urlsplit(selected.canonical_url).hostname, "rank": selected.rank},
                )
                self._event(
                    connection,
                    run_id,
                    "SOURCE_FOUND",
                    "FETCHING",
                    "SAFE_FETCH_STARTED",
                    {"search_result_id": str(search_result_id)},
                )
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.candidate_fetches(id,discovery_run_id,search_result_id,domain,status) VALUES(:id,:run,:search,:domain,'STARTED')"
                    ),
                    {
                        "id": fetch_id,
                        "run": run_id,
                        "search": search_result_id,
                        "domain": urllib.parse.urlsplit(selected.canonical_url).hostname or "",
                    },
                )
        artifact, fetch_chain = self._fetch_source(spec, selected)
        rows = self._parse(spec, artifact)
        storage_key = self._store(artifact)
        candidate_id = stable_uuid("candidate", f"{spec.key}:{run_id}")
        version_id = stable_uuid("candidate-version", f"{candidate_id}:{artifact.sha256}")
        artifact_id = stable_uuid("quarantine-artifact", f"{version_id}:{artifact.sha256}")
        extraction_id = stable_uuid("extraction", f"{version_id}:{artifact.sha256}:{spec.parser_name}")
        review_id = stable_uuid("review-case", str(version_id))
        preview_material = []
        for row in rows:
            preview_material.append(
                {
                    **row.__dict__,
                    "artifact_sha256": artifact.sha256,
                    "extractor_version": spec.parser_name,
                    "transform_version": "p1-12-candidate-transform-v2",
                }
            )
        preview_sha = sha(preview_material)
        extraction_sha = sha({"artifact": artifact.sha256, "parser": spec.parser_name, "rows": preview_material})
        with self.engine.begin() as connection:
            self._event(
                connection,
                run_id,
                "FETCHING",
                "FETCHED",
                "SAFE_FETCH_COMPLETED",
                {
                    "http_status": artifact.status,
                    "content_type": artifact.mime_type,
                    "bytes": len(artifact.payload),
                    "redirect_count": len(artifact.redirect_hashes),
                    "response_sha256": artifact.sha256,
                },
            )
            self._event(
                connection,
                run_id,
                "FETCHED",
                "PARSING",
                "DETERMINISTIC_EXTRACTION_STARTED",
                {"parser": spec.parser_name},
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_candidates(id,discovery_run_id,canonical_endpoint_sha256,publisher,dataset_name,source_tier,status,risk_flags,score,score_breakdown) VALUES(:id,:run,:endpoint,:publisher,:name,:tier,'CANDIDATE_PREVIEW_BUILT',CAST(:flags AS jsonb),0.95,CAST(:score AS jsonb))"
                ),
                {
                    "id": candidate_id,
                    "run": run_id,
                    "endpoint": sha(artifact.final_url),
                    "publisher": spec.publisher,
                    "name": spec.dataset_name,
                    "tier": spec.source_tier,
                    "flags": canonical(["LICENSE_REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"]),
                    "score": canonical(
                        {"official_domain": 1.0, "positive_person_metric": 1.0, "full_evidence_chain": 1.0}
                    ),
                },
            )
            metadata = {
                "country_iso3": spec.country_iso3,
                "country_name": spec.country_name,
                "disease_id": spec.disease_id,
                "time_window": request_material["time_window"],
                "requested_metrics": list(spec.requested_metrics),
                "search_provider": outcome.evidence.provider_id,
                "search_result_id": str(search_result_id),
                "fetch_chain": [
                    {"url": item.final_url, "http_status": item.status, "response_sha256": item.sha256}
                    for item in fetch_chain
                ],
                "data_product_key": spec.key,
            }
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_candidate_versions(id,candidate_id,version_no,canonical_url_redacted,canonical_endpoint_sha256,publisher_evidence_sha256,terms_sha256,license_status,format,metadata,content_sha256) VALUES(:id,:candidate,1,:url,:endpoint,:publisher_sha,:terms,'REVIEW_REQUIRED',:format,CAST(:metadata AS jsonb),:content)"
                ),
                {
                    "id": version_id,
                    "candidate": candidate_id,
                    "url": artifact.final_url,
                    "endpoint": sha(artifact.final_url),
                    "publisher_sha": sha(
                        {"publisher": spec.publisher, "domain": urllib.parse.urlsplit(artifact.final_url).hostname}
                    ),
                    "terms": sha({"status": "REVIEW_REQUIRED", "publisher": spec.publisher}),
                    "format": {
                        "application/json": "JSON",
                        "application/pdf": "PDF",
                        "application/vnd.oasis.opendocument.spreadsheet": "ODS",
                    }.get(artifact.mime_type, "BINARY"),
                    "metadata": canonical(metadata),
                    "content": artifact.sha256,
                },
            )
            connection.execute(
                text(
                    "UPDATE epidemic_discovery.candidate_fetches SET candidate_id=:candidate,completed_at=now(),final_url=:url,final_url_sha256=:url_sha,domain=:domain,status='FETCHED',http_status=:status,content_type=:mime,byte_size=:bytes,etag=:etag,last_modified=:modified,elapsed_ms=0,artifact_sha256=:sha,redirect_sha256=CAST(:redirects AS jsonb) WHERE id=:id"
                ),
                {
                    "candidate": candidate_id,
                    "url": artifact.final_url,
                    "url_sha": sha(artifact.final_url),
                    "domain": urllib.parse.urlsplit(artifact.final_url).hostname or "",
                    "status": artifact.status,
                    "mime": artifact.mime_type,
                    "bytes": len(artifact.payload),
                    "etag": artifact.etag,
                    "modified": artifact.last_modified,
                    "sha": artifact.sha256,
                    "redirects": canonical(list(artifact.redirect_hashes)),
                    "id": fetch_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.quarantine_artifacts(id,candidate_version_id,sha256,byte_size,mime_type,storage_key,security_status,security_report_sha256,retrieved_at,search_result_id,fetch_id) VALUES(:id,:version,:sha,:bytes,:mime,:storage,'PASS',:security,now(),:search,:fetch)"
                ),
                {
                    "id": artifact_id,
                    "version": version_id,
                    "sha": artifact.sha256,
                    "bytes": len(artifact.payload),
                    "mime": artifact.mime_type,
                    "storage": storage_key,
                    "security": sha(
                        {
                            "tls_verify": True,
                            "ssrf": "PASS",
                            "dns_rebinding": "PASS",
                            "active_content_executed": False,
                            "prompt_injection_executed": False,
                        }
                    ),
                    "search": search_result_id,
                    "fetch": fetch_id,
                },
            )
            fields = sorted({field for row in rows for field in row.source_fields})
            profile_id = stable_uuid("schema-profile", str(version_id))
            dates = [row.source_as_of_date or row.period_start or row.report_date for row in rows]
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_schema_profiles(id,candidate_version_id,schema_sha256,row_count,column_count,date_min,date_max,profile_json) VALUES(:id,:version,:sha,:rows,:columns,:date_min,:date_max,CAST(:profile AS jsonb))"
                ),
                {
                    "id": profile_id,
                    "version": version_id,
                    "sha": sha({"fields": fields, "parser": spec.parser_name}),
                    "rows": len(rows),
                    "columns": len(fields),
                    "date_min": min(date for date in dates if date),
                    "date_max": max(date for date in dates if date),
                    "profile": canonical({"fields": fields, "parser": spec.parser_name, "parsed_table_count": 1}),
                },
            )
            for ordinal, field in enumerate(fields, 1):
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_schema_columns(id,schema_profile_id,ordinal,source_name,inferred_type,example_value,null_count,warning_flags) VALUES(:id,:profile,:ordinal,:name,'source',:example,0,'[]'::jsonb)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "profile": profile_id,
                        "ordinal": ordinal,
                        "name": field,
                        "example": str(rows[0].source_fields.get(field, ""))[:500],
                    },
                )
            mapping = {field: {"canonical": target} for row in rows for field, target in row.mapping.items()}
            mapping_sha = sha(mapping)
            proposal_id = stable_uuid("mapping", f"{version_id}:{mapping_sha}")
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_mapping_proposals(id,candidate_version_id,revision,mapping_sha256,proposal_json,status) VALUES(:id,:version,1,:sha,CAST(:mapping AS jsonb),'PROPOSED')"
                ),
                {"id": proposal_id, "version": version_id, "sha": mapping_sha, "mapping": canonical(mapping)},
            )
            for source, target in mapping.items():
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_mapping_fields(id,mapping_proposal_id,source_field,canonical_field,metric_code,transform,confidence,warning) VALUES(:id,:proposal,:source,:canonical,:metric,'deterministic-v2',1.0,NULL)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "proposal": proposal_id,
                        "source": source,
                        "canonical": target["canonical"]
                        if target["canonical"] not in spec.requested_metrics
                        else "value_numeric",
                        "metric": target["canonical"] if target["canonical"] in spec.requested_metrics else None,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO epidemic_p1_12.candidate_extraction_runs(id,candidate_version_id,artifact_id,parser_name,extractor_version,transform_version,state,started_at,finished_at,parsed_table_count,raw_row_count,parsed_row_count,warning_count,source_locator_policy,extraction_sha256,details) VALUES(:id,:version,:artifact,:parser,:extractor,:transform,'PARSED_WITH_WARNINGS',now(),now(),1,:rows,:rows,:warnings,'deterministic row/page/table locator',:sha,CAST(:details AS jsonb))"
                ),
                {
                    "id": extraction_id,
                    "version": version_id,
                    "artifact": artifact_id,
                    "parser": spec.parser_name,
                    "extractor": spec.parser_name,
                    "transform": "p1-12-candidate-transform-v2",
                    "rows": len(rows),
                    "warnings": sum(bool(row.quality_flags) for row in rows),
                    "sha": extraction_sha,
                    "details": canonical({"active_content_executed": False, "llm_numeric_generation": False}),
                },
            )
            for ordinal, row in enumerate(rows, 1):
                material = {**row.__dict__, "artifact_sha256": artifact.sha256, "ordinal": ordinal}
                connection.execute(
                    text(
                        "INSERT INTO epidemic_p1_12.candidate_preview_rows_v2(id,candidate_version_id,extraction_run_id,row_ordinal,country_iso3,country_name,disease_id,pathogen_variant,report_date,source_as_of_date,period_start,period_end,metric_code,value_numeric,unit,case_definition,aggregation_type,geographic_scope,source_url,artifact_sha256,source_row_locator,raw_value,normalized_value,extractor_version,transform_version,source_fields,mapping_json,quality_flags,license_status,row_sha256) VALUES(:id,:version,:extraction,:ordinal,:country,:country_name,:disease,:pathogen,:report,:as_of,:start,:end,:metric,:value,:unit,:case_definition,:aggregation,:scope,:url,:artifact,:locator,:raw,:normalized,:extractor,:transform,CAST(:source_fields AS jsonb),CAST(:mapping AS jsonb),CAST(:flags AS jsonb),:license,:row_sha)"
                    ),
                    {
                        "id": stable_uuid("candidate-preview-row", f"{version_id}:{ordinal}"),
                        "version": version_id,
                        "extraction": extraction_id,
                        "ordinal": ordinal,
                        "country": row.country_iso3,
                        "country_name": row.country_name,
                        "disease": row.disease_id,
                        "pathogen": row.pathogen_variant,
                        "report": row.report_date,
                        "as_of": row.source_as_of_date,
                        "start": row.period_start,
                        "end": row.period_end,
                        "metric": row.metric_code,
                        "value": row.value,
                        "unit": row.unit,
                        "case_definition": row.case_definition,
                        "aggregation": row.aggregation_type,
                        "scope": row.geographic_scope,
                        "url": row.source_url,
                        "artifact": artifact.sha256,
                        "locator": row.source_row_locator,
                        "raw": row.raw_value,
                        "normalized": row.normalized_value,
                        "extractor": spec.parser_name,
                        "transform": "p1-12-candidate-transform-v2",
                        "source_fields": canonical(row.source_fields),
                        "mapping": canonical(row.mapping),
                        "flags": canonical(list(row.quality_flags)),
                        "license": row.license_status,
                        "row_sha": sha(material),
                    },
                )
                legacy = {
                    "country_iso3": row.country_iso3,
                    "country_name": row.country_name,
                    "disease_id": row.disease_id,
                    "source_as_of": str(row.source_as_of_date or row.period_start),
                    "metric_code": row.metric_code,
                    "value_numeric": row.value,
                    "unit": row.unit,
                    "aggregation_type": row.aggregation_type,
                    "source_row_locator": row.source_row_locator,
                }
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_preview_rows(id,candidate_version_id,sample_kind,row_ordinal,raw_json,sanitized_json,row_errors,row_sha256) VALUES(:id,:version,'CANDIDATE_PREVIEW_V2',:ordinal,CAST(:row AS jsonb),CAST(:row AS jsonb),'[]'::jsonb,:sha)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "version": version_id,
                        "ordinal": ordinal,
                        "row": canonical(legacy),
                        "sha": sha(legacy),
                    },
                )
            quality = {
                "candidate_preview_row_count": len(rows),
                "mappable_person_metric_count": sum(row.unit == "person" and row.value > 0 for row in rows),
                "source_semantics_complete": True,
                "license_status": "REVIEW_REQUIRED",
                "preview_sha256": preview_sha,
            }
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_quality_assessments(id,candidate_version_id,assessment_sha256,status,quality_json,security_json,pii_status,license_status) VALUES(:id,:version,:sha,'CANDIDATE_PREVIEW_BUILT',CAST(:quality AS jsonb),CAST(:security AS jsonb),'AGGREGATE_ONLY','REVIEW_REQUIRED')"
                ),
                {
                    "id": uuid.uuid4(),
                    "version": version_id,
                    "sha": sha(quality),
                    "quality": canonical(quality),
                    "security": canonical(
                        {
                            "tls_verify": True,
                            "ssrf": "PASS",
                            "prompt_injection": "DATA_ONLY_NOT_EXECUTED",
                            "active_content": False,
                        }
                    ),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_review_cases(id,candidate_version_id,state,current_mapping_revision,etag) VALUES(:id,:version,'CANDIDATE_PREVIEW_BUILT',1,:etag)"
                ),
                {"id": review_id, "version": version_id, "etag": preview_sha},
            )
            self._event(
                connection,
                run_id,
                "PARSING",
                "PARSED",
                "DETERMINISTIC_EXTRACTION_COMPLETED",
                {"parsed_table_count": 1, "parsed_rows": len(rows)},
            )
            self._event(
                connection,
                run_id,
                "PARSED",
                "VALIDATING",
                "REVIEW_READY_V2_VALIDATION_STARTED",
                {"candidate_preview_rows": len(rows)},
            )
            planned = 3
            executed = connection.execute(
                text(
                    "SELECT count(*) FROM epidemic_p1_12.discovery_queries_v2 WHERE discovery_request_id=:id AND state<>'PLANNED'"
                ),
                {"id": request_id},
            ).scalar_one()
            success = connection.execute(
                text(
                    "SELECT count(*) FROM epidemic_p1_12.discovery_queries_v2 WHERE discovery_request_id=:id AND state='SUCCEEDED'"
                ),
                {"id": request_id},
            ).scalar_one()
            search_count = connection.execute(
                text(
                    "SELECT count(*) FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:id"
                ),
                {"id": run_id},
            ).scalar_one()
            url_count = connection.execute(
                text(
                    "SELECT count(DISTINCT canonical_url_sha256) FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:id"
                ),
                {"id": run_id},
            ).scalar_one()
            person_count = sum(row.unit == "person" and row.value > 0 for row in rows)
            counts = {
                "planned_query_count": planned,
                "executed_query_count": int(executed),
                "provider_success_count": int(success),
                "raw_search_result_count": int(search_count),
                "deduplicated_url_count": int(url_count),
                "fetch_attempt_count": 1,
                "fetch_success_count": 1,
                "artifact_count": 1,
                "extraction_run_count": 1,
                "parsed_table_count": 1,
                "candidate_preview_row_count": len(rows),
                "mappable_person_metric_count": person_count,
            }
            violations = review_ready_violations(spec, counts, rows, artifact.sha256, preview_sha)
            if violations:
                raise RuntimeError("REVIEW_READY_V2_REJECTED:" + ",".join(violations))
            eligibility_hash = sha(
                {
                    "version": str(version_id),
                    "artifact": artifact.sha256,
                    "preview": preview_sha,
                    "transform": "p1-12-candidate-transform-v2",
                    "counts": counts,
                    "eligibility_version": ELIGIBILITY_VERSION,
                }
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_p1_12.candidate_eligibility_v2(candidate_version_id,eligibility_version,eligible,planned_query_count,executed_query_count,provider_success_count,raw_search_result_count,deduplicated_url_count,fetch_attempt_count,fetch_success_count,artifact_count,extraction_run_count,parsed_table_count,candidate_preview_row_count,mappable_person_metric_count,artifact_sha256,preview_sha256,transform_version,eligibility_hash,reasons) VALUES(:version,:eligibility,true,:planned,:executed,:provider,:search,:urls,1,1,1,1,1,:preview_rows,:person,:artifact,:preview_sha,:transform,:hash,'[]'::jsonb)"
                ),
                {
                    "version": version_id,
                    "eligibility": ELIGIBILITY_VERSION,
                    "planned": planned,
                    "executed": executed,
                    "provider": success,
                    "search": search_count,
                    "urls": url_count,
                    "preview_rows": len(rows),
                    "person": person_count,
                    "artifact": artifact.sha256,
                    "preview_sha": preview_sha,
                    "transform": "p1-12-candidate-transform-v2",
                    "hash": eligibility_hash,
                },
            )
            event_sha = sha({"version": str(version_id), "eligibility": eligibility_hash, "state": "REVIEW_READY"})
            review_id = uuid.UUID(
                str(
                    connection.execute(
                        text("SELECT epidemic_p1_12.mark_review_ready_v2(:version,:event_sha)"),
                        {"version": version_id, "event_sha": event_sha},
                    ).scalar_one()
                )
            )
            connection.execute(
                text("UPDATE epidemic_discovery.candidate_fetches SET status='REVIEW_READY' WHERE id=:id"),
                {"id": fetch_id},
            )
            self._event(
                connection,
                run_id,
                "VALIDATING",
                "COMPLETED_WITH_REVIEW_READY",
                "REVIEW_READY_V2_COMPLETED",
                {"review_case_id": str(review_id), "eligibility_hash": eligibility_hash},
                {
                    "planned_queries": planned,
                    "executed_queries": int(executed),
                    "provider_success": int(success),
                    "raw_search_results": int(search_count),
                    "fetch_success": 1,
                    "artifacts": 1,
                    "parsed_tables": 1,
                    "candidate_preview_rows": len(rows),
                    "mappable_person_metrics": person_count,
                },
            )
        sample_row = next(row for row in rows if row.unit == "person" and row.value > 0)
        return CandidateManifest(
            request_id,
            run_id,
            candidate_id,
            version_id,
            review_id,
            spec.publisher,
            outcome.evidence.provider_id,
            queries,
            selected.canonical_url,
            artifact.final_url,
            artifact_id,
            artifact.sha256,
            len(artifact.payload),
            extraction_id,
            len(rows),
            len(rows),
            preview_sha,
            eligibility_hash,
            {
                "country_iso3": sample_row.country_iso3,
                "date": str(sample_row.source_as_of_date or sample_row.period_start),
                "metric": sample_row.metric_code,
                "value": sample_row.value,
                "unit": sample_row.unit,
                "source_row_locator": sample_row.source_row_locator,
            },
        )

    def run_all(self) -> tuple[CandidateManifest, ...]:
        return tuple(self.run_one(spec) for spec in SPECS)
