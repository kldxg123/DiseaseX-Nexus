from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text

from epidemic_core.ebola.fetcher import FetchResult, OfficialSourceFetcher
from epidemic_core.ebola.parsers import EbolaObservation, ParsedReport, ParseError, parse_who_don
from epidemic_core.p1_12 import ADAPTER_VERSION, TRANSFORM_VERSION

EBOLA_CONTRACT = "trusted-ebola-who-bvd-2026"
INFLUENZA_CONTRACT = "trusted-influenza-who-flumart"
EVENT_URL = "https://www.who.int/emergencies/emergency-events/item/2026-e000253"
OUTPUTS_URL = (
    "https://www.who.int/teams/global-influenza-programme/surveillance-and-monitoring/influenza-surveillance-outputs"
)
TERMS_URL = "https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions"
FLUID_ENDPOINT = "https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI"
FLUNET_ENDPOINT = "https://xmart-api-public.who.int/FLUMART/VIW_FNT"
METADATA_ENDPOINT = "https://xmart-api-public.who.int/FLUMART/VIW_FLU_METADATA"
ALLOWED_HOSTS = {"www.who.int", "xmart-api-public.who.int"}
QUALITY_RULESET = "p1-12-trusted-official-quality-v1"
COUNTRY_MAPPING = "iso-3166-1-2025-11-21"
SNAPSHOT_SCHEMA = 2


def create_trusted_ingest_engine() -> Engine:
    """Build the formal-writer connection from its dedicated secret identity."""

    database_url = os.environ.get("TRUSTED_INGEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TRUSTED_INGEST_DATABASE_URL_REQUIRED")
    engine = create_engine(
        database_url,
        pool_size=2,
        max_overflow=1,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _secure_session(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET search_path TO pg_catalog")
            cursor.execute("SET timezone TO 'UTC'")
        finally:
            cursor.close()

    return engine


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: object) -> str:
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"epidemic-openclaw:p1-12:{kind}:{value}")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)


@dataclass(frozen=True)
class TrustedRow:
    country_alpha2: str
    country_iso3: str
    country_name: str
    disease_slug: str
    metric_code: str
    value: int
    unit: str
    period_start: dt.date
    period_end: dt.date
    granularity: str
    reporting_date: dt.date | None
    source_as_of: dt.date
    dimensions: dict[str, object]
    source_url: str
    source_row_locator: str
    artifact_sha256: str
    case_definition: str
    aggregation_type: str
    pathogen_variant: str | None = None
    source_report_delta: bool = False
    revision_notice: bool = False


@dataclass(frozen=True)
class IngestionResult:
    source_key: str
    country_iso3: str
    observation_count: int
    observations_added: int
    observations_revised: int
    snapshot_id: uuid.UUID
    snapshot_sha256: str
    snapshot_created: bool
    date_min: dt.date
    date_max: dt.date
    artifact_sha256s: tuple[str, ...]


class TrustedIngestionService:
    def __init__(self, engine: Engine, project_root: Path) -> None:
        self.engine = engine
        self.project_root = project_root
        self.raw_root = project_root / "storage" / "p1_12" / "trusted" / "raw" / "sha256"
        self.fetcher = OfficialSourceFetcher(
            ALLOWED_HOSTS,
            max_bytes=48 * 1024 * 1024,
            use_environment_proxy=os.environ.get("P1_12_ALLOW_DOWNLOAD_PROXY") == "1",
        )

    def _store(self, result: FetchResult) -> str:
        suffix = {
            "application/json": ".json",
            "text/html": ".html",
            "text/plain": ".txt",
        }.get(result.mime_type, ".bin")
        target = self.raw_root / result.sha256[:2] / f"{result.sha256}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(result.payload)
        return str(target.relative_to(self.project_root))

    @staticmethod
    def _don_urls(index: FetchResult) -> tuple[str, ...]:
        parser = LinkParser()
        parser.feed(index.payload.decode("utf-8", errors="replace"))
        urls: set[str] = set()
        for link in parser.links:
            url = urllib.parse.urljoin(index.final_url, link)
            parsed = urllib.parse.urlsplit(url)
            match = re.search(r"/emergencies/disease-outbreak-news/item/(2026-DON\d+)$", parsed.path, re.I)
            if parsed.scheme == "https" and parsed.hostname == "www.who.int" and match:
                urls.add(f"https://www.who.int/emergencies/disease-outbreak-news/item/{match.group(1).upper()}")
        if len(urls) < 5:
            # The event page must be the discovery authority. A sparse or redesigned page is drift, not permission
            # to silently fall back to a hard-coded list.
            raise RuntimeError("WHO_EVENT_INDEX_DON_SERIES_DRIFT")
        return tuple(sorted(urls, key=lambda value: int(re.search(r"DON(\d+)", value).group(1))))  # type: ignore[union-attr]

    @staticmethod
    def _locator(report: ParsedReport, item: EbolaObservation) -> str:
        return (
            f"article[role=main] paragraph containing country=Democratic Republic of the Congo; "
            f"source_as_of={report.source_as_of.isoformat()}; metric={item.metric_code}; "
            f"text-evidence-sha256={sha({'date': report.source_as_of, 'metric': item.metric_code, 'value': item.value})}"
        )

    def _ebola_rows(self) -> tuple[list[TrustedRow], list[FetchResult]]:
        index = self.fetcher.fetch(EVENT_URL)
        terms = self.fetcher.fetch(TERMS_URL)
        results = [index, terms]
        rows: list[TrustedRow] = []
        for url in self._don_urls(index):
            fetched = self.fetcher.fetch(url)
            results.append(fetched)
            try:
                parsed = parse_who_don(fetched.payload, url.rsplit("/", 1)[-1])
            except ParseError:
                # WHO event pages can include navigation links to other DON items. They are fetched and
                # retained as discovery evidence but cannot enter the BDBV series without the COD section.
                continue
            for item in parsed.observations:
                if item.country_iso3 != "COD" or item.value is None:
                    continue
                rows.append(
                    TrustedRow(
                        "CD",
                        "COD",
                        "Democratic Republic of the Congo",
                        "ebola-disease",
                        item.metric_code,
                        int(item.value),
                        "person",
                        item.source_as_of,
                        item.source_as_of,
                        "irregular",
                        parsed.publication_date,
                        item.source_as_of,
                        {
                            "pathogen_variant": "BDBV",
                            "case_classification": item.case_classification,
                            "import_classification": item.import_classification,
                            "geographic_scope": item.geography_scope,
                            "source_date_type": "source_as_of",
                            "reporting_country": item.reporting_country,
                            "exposure_country": item.exposure_country,
                            "diagnosis_country": item.diagnosis_country,
                            "treatment_country": item.treatment_country,
                            "backfill_revision_possible": True,
                        },
                        fetched.final_url,
                        self._locator(parsed, item),
                        fetched.sha256,
                        item.case_classification,
                        "cumulative",
                        "BDBV",
                    )
                )
        distinct_dates = sorted({row.source_as_of for row in rows})
        if len(distinct_dates) < 5:
            raise RuntimeError("TRUSTED_EBOLA_FIVE_REPORT_DATES_REQUIRED")
        by_metric: dict[str, list[TrustedRow]] = {}
        for row in rows:
            by_metric.setdefault(row.metric_code, []).append(row)
        adjusted: list[TrustedRow] = []
        for metric_rows in by_metric.values():
            previous: int | None = None
            for row in sorted(metric_rows, key=lambda item: item.source_as_of):
                delta = None if previous is None else row.value - previous
                dimensions = {**row.dimensions, "derived_report_delta": delta, "derived_delta_not_incidence": True}
                adjusted.append(
                    TrustedRow(
                        **{
                            **row.__dict__,
                            "dimensions": dimensions,
                            "source_report_delta": delta is not None,
                            "revision_notice": bool(delta is not None and delta < 0),
                        }
                    )
                )
                previous = row.value
        return sorted(adjusted, key=lambda row: (row.source_as_of, row.metric_code)), results

    @staticmethod
    def _odata_url(endpoint: str, country: str, top: int) -> str:
        query = urllib.parse.urlencode(
            {
                "$filter": f"COUNTRY_CODE eq '{country}' and ISO_YEAR ge 2025",
                "$orderby": "ISOYW desc",
                "$top": str(top),
                "$format": "json",
            }
        )
        return f"{endpoint}?{query}"

    @staticmethod
    def _json_rows(result: FetchResult) -> list[dict[str, Any]]:
        value = json.loads(result.payload)
        rows = value.get("value", value) if isinstance(value, dict) else value
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise RuntimeError("WHO_ODATA_SHAPE_DRIFT")
        return rows

    @staticmethod
    def _select_influenza_country(rows: list[dict[str, Any]]) -> tuple[str, str]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("COUNTRY_CODE") or "")
            date = str(row.get("ISO_WEEKSTARTDATE") or "")
            if not re.fullmatch(r"[A-Z]{3}", code) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                continue
            item = grouped.setdefault(
                code,
                {"name": str(row.get("COUNTRY_AREA_TERRITORY") or code), "weeks": set(), "latest": "", "positive": 0},
            )
            values = [row.get(key) for key in ("ILI_CASE", "SARI_CASE", "SARI_DEATHS")]
            if any(isinstance(value, (int, float)) for value in values):
                cast_weeks = item["weeks"]
                assert isinstance(cast_weeks, set)
                cast_weeks.add(int(row["ISOYW"]))
            if any(isinstance(value, (int, float)) and value > 0 for value in values):
                item["positive"] = int(item["positive"]) + 1
            item["latest"] = max(str(item["latest"]), date)
        eligible = [
            (code, str(item["name"]), str(item["latest"]), len(item["weeks"]))
            for code, item in grouped.items()
            if len(item["weeks"]) >= 8 and int(item["positive"]) > 0
        ]
        china = next((item for item in eligible if item[0] == "CHN"), None)
        selected = china or max(eligible, key=lambda item: (item[2], item[3], item[0]), default=None)
        if selected is None:
            raise RuntimeError("WHO_FLUID_RECENT_PERSON_SERIES_REQUIRED")
        return selected[0], selected[1]

    @staticmethod
    def _weekly_aggregate(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[int, dict[str, int]]:
        by_week: dict[int, dict[str, int]] = {}
        for row in rows:
            week = row.get("ISOYW")
            if not isinstance(week, int):
                continue
            target = by_week.setdefault(week, {field: 0 for field in fields})
            for field in fields:
                value = row.get(field)
                if isinstance(value, (int, float)) and value >= 0:
                    target[field] += int(value)
        return by_week

    def _influenza_rows(self) -> tuple[list[TrustedRow], list[FetchResult], str]:
        outputs = self.fetcher.fetch(OUTPUTS_URL)
        terms = self.fetcher.fetch(TERMS_URL)
        metadata = self.fetcher.fetch(f"{METADATA_ENDPOINT}?$top=200&$format=json")
        global_recent = self.fetcher.fetch(
            f"{FLUID_ENDPOINT}?$filter=ISO_YEAR%20ge%202025&$orderby=ISOYW%20desc&$top=5000&$format=json"
        )
        global_rows = self._json_rows(global_recent)
        country, country_name = self._select_influenza_country(global_rows)
        fluid = self.fetcher.fetch(self._odata_url(FLUID_ENDPOINT, country, 5000))
        flunet = self.fetcher.fetch(self._odata_url(FLUNET_ENDPOINT, country, 5000))
        fluid_rows, flunet_rows = self._json_rows(fluid), self._json_rows(flunet)
        if country == "CHN":
            alpha2 = "CN"
        elif country == "HND":
            alpha2 = "HN"
        else:
            with self.engine.connect() as connection:
                alpha2 = connection.execute(
                    text("SELECT alpha2 FROM epidemic.countries WHERE alpha3=:country"), {"country": country}
                ).scalar_one()
        person_fields = {
            "ILI_CASE": "influenza_ili_cases",
            "SARI_CASE": "influenza_sari_cases",
            "SARI_DEATHS": "influenza_sari_deaths",
        }
        lab_fields = {
            "SPEC_PROCESSED_NB": ("influenza_specimens_processed", "specimen"),
            "INF_ALL": ("influenza_positive_detections", "detection"),
            "INF_A": ("influenza_a_detections", "detection"),
            "INF_B": ("influenza_b_detections", "detection"),
            "AH1N12009": ("influenza_a_h1n1pdm09_detections", "detection"),
            "AH3": ("influenza_a_h3n2_detections", "detection"),
        }
        fluid_agg = self._weekly_aggregate(fluid_rows, tuple(person_fields))
        flunet_agg = self._weekly_aggregate(flunet_rows, tuple(lab_fields))
        date_by_week = {
            int(row["ISOYW"]): dt.date.fromisoformat(str(row["ISO_WEEKSTARTDATE"])[:10])
            for row in [*fluid_rows, *flunet_rows]
            if isinstance(row.get("ISOYW"), int)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("ISO_WEEKSTARTDATE")))
        }
        latest = max(date_by_week.values())
        cutoff = latest - dt.timedelta(days=364)
        rows: list[TrustedRow] = []
        for week, values in sorted(fluid_agg.items()):
            start = date_by_week.get(week)
            if start is None or start < cutoff:
                continue
            for source_field, metric in person_fields.items():
                value = values[source_field]
                rows.append(
                    TrustedRow(
                        alpha2,
                        country,
                        country_name,
                        "influenza",
                        metric,
                        value,
                        "person",
                        start,
                        start + dt.timedelta(days=6),
                        "week",
                        None,
                        start + dt.timedelta(days=6),
                        {
                            "source_date_type": "iso_surveillance_week",
                            "iso_year_week": week,
                            "syndromic_surveillance": True,
                            "not_laboratory_confirmed_influenza": True,
                            "missing_weeks_interpolated": False,
                        },
                        fluid.final_url,
                        f"OData VIW_FID_EPI row group COUNTRY_CODE={country};ISOYW={week};field={source_field}",
                        fluid.sha256,
                        "WHO FluID syndromic surveillance",
                        "period_total",
                    )
                )
        for week, values in sorted(flunet_agg.items()):
            start = date_by_week.get(week)
            if start is None or start < cutoff:
                continue
            for source_field, (metric, unit) in lab_fields.items():
                value = values[source_field]
                rows.append(
                    TrustedRow(
                        alpha2,
                        country,
                        country_name,
                        "influenza",
                        metric,
                        value,
                        unit,
                        start,
                        start + dt.timedelta(days=6),
                        "week",
                        None,
                        start + dt.timedelta(days=6),
                        {
                            "source_date_type": "iso_surveillance_week",
                            "iso_year_week": week,
                            "laboratory_surveillance": True,
                            "not_person_case_count": True,
                            "missing_weeks_interpolated": False,
                        },
                        flunet.final_url,
                        f"OData VIW_FNT row group COUNTRY_CODE={country};ISOYW={week};field={source_field}",
                        flunet.sha256,
                        "WHO FluNet virological surveillance",
                        "period_total",
                    )
                )
        person_weeks = {row.period_start for row in rows if row.unit == "person" and row.value >= 0}
        if len(person_weeks) < 8 or not any(row.unit == "person" and row.value > 0 for row in rows):
            raise RuntimeError("TRUSTED_INFLUENZA_EIGHT_PERSON_WEEKS_REQUIRED")
        return rows, [outputs, terms, metadata, global_recent, fluid, flunet], country

    def _persist_fetches(
        self,
        connection: Any,
        contract_id: uuid.UUID,
        retrieval_id: uuid.UUID,
        source_id: uuid.UUID,
        results: list[FetchResult],
    ) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
        output: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
        for result in results:
            storage = self._store(result)
            artifact_id = stable_uuid("formal-artifact", result.sha256)
            fetch_id = stable_uuid("formal-fetch", f"{retrieval_id}:{result.final_url}:{result.sha256}")
            connection.execute(
                text(
                    "INSERT INTO epidemic.raw_artifacts(id,sha256,byte_size,media_type,encoding,storage_mode,storage_uri) VALUES(:id,:sha,:bytes,:mime,NULL,'file',:storage) ON CONFLICT(sha256) DO NOTHING"
                ),
                {
                    "id": artifact_id,
                    "sha": result.sha256,
                    "bytes": len(result.payload),
                    "mime": result.mime_type,
                    "storage": storage,
                },
            )
            artifact_id = connection.execute(
                text("SELECT id FROM epidemic.raw_artifacts WHERE sha256=:sha"), {"sha": result.sha256}
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO epidemic.source_fetches(id,retrieval_run_id,source_id,canonical_url,requested_at,fetched_at,http_status,etag,last_modified,artifact_id,attempt_number,response_content_type,response_byte_size) VALUES(:id,:retrieval,:source,:url,now(),now(),:status,:etag,:modified,:artifact,1,:mime,:bytes) ON CONFLICT(id) DO NOTHING"
                ),
                {
                    "id": fetch_id,
                    "retrieval": retrieval_id,
                    "source": source_id,
                    "url": result.final_url,
                    "status": result.status,
                    "etag": result.etag,
                    "modified": result.last_modified,
                    "artifact": artifact_id,
                    "mime": result.mime_type,
                    "bytes": len(result.payload),
                },
            )
            evidence_id = stable_uuid("trusted-fetch-evidence", f"{contract_id}:{result.final_url}:{result.sha256}")
            connection.execute(
                text(
                    "INSERT INTO epidemic_p1_12.trusted_fetch_artifacts(id,source_contract_id,canonical_url,http_status,mime_type,byte_size,etag,last_modified,response_sha256,storage_key,retrieved_at,elapsed_ms,redirect_chain,source_row_locator_policy) VALUES(:id,:contract,:url,:status,:mime,:bytes,:etag,:modified,:sha,:storage,now(),0,CAST(:redirects AS jsonb),'deterministic field/paragraph/table locator') ON CONFLICT(source_contract_id,canonical_url,response_sha256) DO NOTHING"
                ),
                {
                    "id": evidence_id,
                    "contract": contract_id,
                    "url": result.final_url,
                    "status": result.status,
                    "mime": result.mime_type,
                    "bytes": len(result.payload),
                    "etag": result.etag,
                    "modified": result.last_modified,
                    "sha": result.sha256,
                    "storage": storage,
                    "redirects": canonical(list(result.redirect_hashes)),
                },
            )
            evidence_id = connection.execute(
                text(
                    "SELECT id FROM epidemic_p1_12.trusted_fetch_artifacts WHERE source_contract_id=:contract AND canonical_url=:url AND response_sha256=:sha"
                ),
                {"contract": contract_id, "url": result.final_url, "sha": result.sha256},
            ).scalar_one()
            output[result.sha256] = (uuid.UUID(str(fetch_id)), uuid.UUID(str(evidence_id)))
        return output

    def _persist(self, contract_key: str, rows: list[TrustedRow], results: list[FetchResult]) -> IngestionResult:
        if not rows:
            raise RuntimeError("TRUSTED_ROWS_EMPTY")
        country = rows[0].country_iso3
        source_key = "who_bvd_2026_official" if contract_key == EBOLA_CONTRACT else "who_fluid_flunet_official"
        source_name = "WHO 2026 BDBV event series" if contract_key == EBOLA_CONTRACT else "WHO FluID and FluNet"
        disease_slug = rows[0].disease_slug
        manifest_sha = sha([row.__dict__ for row in rows])
        now = dt.datetime.now(dt.timezone.utc)
        with self.engine.begin() as connection:
            contract = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_p1_12.trusted_source_contracts WHERE source_key=:key AND active FOR UPDATE"
                    ),
                    {"key": contract_key},
                )
                .mappings()
                .one()
            )
            contract_id = uuid.UUID(str(contract["id"]))
            disease_id = connection.execute(
                text("SELECT id FROM epidemic.diseases WHERE slug=:slug"), {"slug": disease_slug}
            ).scalar_one()
            source_id = stable_uuid("data-source", source_key)
            connection.execute(
                text(
                    "INSERT INTO epidemic.data_sources(id,source_key,display_name,source_type,authority,base_url,adapter_name,license_expression,terms_url,enabled) VALUES(:id,:key,:name,'official_api','World Health Organization',:url,:adapter,'WHO_DATA_TERMS',:terms,true) ON CONFLICT(source_key) DO NOTHING"
                ),
                {
                    "id": source_id,
                    "key": source_key,
                    "name": source_name,
                    "url": str(contract["canonical_url"]),
                    "adapter": ADAPTER_VERSION,
                    "terms": str(contract["terms_url"]),
                },
            )
            source_id = uuid.UUID(
                str(
                    connection.execute(
                        text("SELECT id FROM epidemic.data_sources WHERE source_key=:key"), {"key": source_key}
                    ).scalar_one()
                )
            )
            query_fingerprint = sha(
                {"country": country, "disease": disease_slug, "window": "all_saved_official", "source": source_key}
            )
            query_id = stable_uuid("query", query_fingerprint)
            connection.execute(
                text(
                    "INSERT INTO epidemic.query_requests(id,fingerprint,raw_country,raw_disease,country_alpha2,disease_id,start_date,end_date,time_window_kind,duration_iso8601,source_preference,schema_version) VALUES(:id,:fingerprint,:country,:disease,:alpha2,:disease_id,NULL,NULL,'latest_rolling_year','P1Y','official',2) ON CONFLICT(fingerprint) DO NOTHING"
                ),
                {
                    "id": query_id,
                    "fingerprint": query_fingerprint,
                    "country": country,
                    "disease": disease_slug,
                    "alpha2": rows[0].country_alpha2,
                    "disease_id": disease_id,
                },
            )
            query_id = uuid.UUID(
                str(
                    connection.execute(
                        text("SELECT id FROM epidemic.query_requests WHERE fingerprint=:fingerprint"),
                        {"fingerprint": query_fingerprint},
                    ).scalar_one()
                )
            )
            retrieval_id = stable_uuid("retrieval", f"{source_key}:{manifest_sha}")
            connection.execute(
                text(
                    "INSERT INTO epidemic.retrieval_runs(id,run_key,query_request_id,source_id,adapter_version,status,attempt_number,started_at,finished_at) VALUES(:id,:key,:query,:source,:adapter,'succeeded',1,:now,:now) ON CONFLICT(run_key) DO NOTHING"
                ),
                {
                    "id": retrieval_id,
                    "key": f"p1-12:{source_key}:{manifest_sha}",
                    "query": query_id,
                    "source": source_id,
                    "adapter": ADAPTER_VERSION,
                    "now": now,
                },
            )
            retrieval_id = uuid.UUID(
                str(
                    connection.execute(
                        text("SELECT id FROM epidemic.retrieval_runs WHERE run_key=:key"),
                        {"key": f"p1-12:{source_key}:{manifest_sha}"},
                    ).scalar_one()
                )
            )
            fetch_map = self._persist_fetches(connection, contract_id, retrieval_id, source_id, results)
            terms = next(result for result in results if result.final_url.rstrip("/") == TERMS_URL.rstrip("/"))
            connection.execute(
                text(
                    "UPDATE epidemic_p1_12.trusted_source_contracts SET terms_artifact_sha256=:sha,updated_at=now() WHERE id=:id AND terms_artifact_sha256 IS DISTINCT FROM :sha"
                ),
                {"sha": terms.sha256, "id": contract_id},
            )
            added = revised = 0
            accepted_ids: list[uuid.UUID] = []
            for row in rows:
                dimensions_sha = sha(row.dimensions)
                series_key = sha(
                    {
                        "source": source_key,
                        "country": row.country_iso3,
                        "disease": disease_slug,
                        "metric": row.metric_code,
                        "period_start": row.period_start,
                        "period_end": row.period_end,
                        "dimensions": row.dimensions,
                    }
                )
                existing = (
                    connection.execute(
                        text(
                            "SELECT o.id,o.revision,o.value_numeric FROM epidemic.normalized_observations o JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id WHERE l.source_contract_id=:contract AND o.series_fingerprint=:series AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id) ORDER BY o.revision DESC LIMIT 1"
                        ),
                        {"contract": contract_id, "series": series_key},
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None and int(existing["value_numeric"]) == row.value:
                    accepted_ids.append(uuid.UUID(str(existing["id"])))
                    continue
                revision = 1 if existing is None else int(existing["revision"]) + 1
                observation_id = stable_uuid(
                    "observation", f"{series_key}:{revision}:{row.value}:{row.artifact_sha256}"
                )
                record_sha = sha(
                    {"series": series_key, "revision": revision, "value": row.value, "artifact": row.artifact_sha256}
                )
                fetch_id, evidence_id = fetch_map[row.artifact_sha256]
                connection.execute(
                    text(
                        "INSERT INTO epidemic.normalized_observations(id,record_fingerprint,series_fingerprint,revision,supersedes_observation_id,source_fetch_id,country_alpha2,disease_id,metric_code,value_numeric,raw_value,period_start,period_end,granularity,reporting_date,as_of,dimensions,dimensions_sha256,normalization_version) VALUES(:id,:record,:series,:revision,:prior,:fetch,:country,:disease,:metric,:value,:raw,:start,:end,:granularity,:reporting,:as_of,CAST(:dimensions AS jsonb),:dimensions_sha,:version)"
                    ),
                    {
                        "id": observation_id,
                        "record": record_sha,
                        "series": series_key,
                        "revision": revision,
                        "prior": None if existing is None else existing["id"],
                        "fetch": fetch_id,
                        "country": row.country_alpha2,
                        "disease": disease_id,
                        "metric": row.metric_code,
                        "value": row.value,
                        "raw": str(row.value),
                        "start": row.period_start,
                        "end": row.period_end,
                        "granularity": row.granularity,
                        "reporting": row.reporting_date,
                        "as_of": dt.datetime.combine(row.source_as_of, dt.time(), dt.timezone.utc),
                        "dimensions": canonical(row.dimensions),
                        "dimensions_sha": dimensions_sha,
                        "version": TRANSFORM_VERSION,
                    },
                )
                flags = ["SOURCE_BACKFILL_REVISION_POSSIBLE"]
                if row.revision_notice:
                    flags.append("NEGATIVE_DERIVED_REPORT_DELTA_SOURCE_REVISION")
                if row.unit != "person":
                    flags.append("NON_PERSON_LABORATORY_UNIT")
                connection.execute(
                    text(
                        "INSERT INTO epidemic.quality_assessments(id,observation_id,ruleset_version,status,score,flags) VALUES(:id,:observation,:rules,'accepted',1.0,CAST(:flags AS jsonb))"
                    ),
                    {
                        "id": stable_uuid("quality", str(observation_id)),
                        "observation": observation_id,
                        "rules": QUALITY_RULESET,
                        "flags": canonical(flags),
                    },
                )
                lineage_sha = sha(
                    {
                        "observation": str(observation_id),
                        "contract": str(contract_id),
                        "artifact": row.artifact_sha256,
                        "locator": row.source_row_locator,
                    }
                )
                connection.execute(
                    text(
                        "INSERT INTO epidemic_p1_12.trusted_observation_lineage(observation_id,source_contract_id,fetch_artifact_id,country_iso3,pathogen_variant,case_definition,aggregation_type,source_as_of_date,source_url,source_row_locator,artifact_sha256,extractor_version,transform_version,source_report_delta,revision_notice,lineage_sha256) VALUES(:observation,:contract,:artifact_id,:country,:pathogen,:case_definition,:aggregation,:source_as_of,:url,:locator,:sha,:extractor,:transform,:delta,:revision_notice,:lineage)"
                    ),
                    {
                        "observation": observation_id,
                        "contract": contract_id,
                        "artifact_id": evidence_id,
                        "country": row.country_iso3,
                        "pathogen": row.pathogen_variant,
                        "case_definition": row.case_definition,
                        "aggregation": row.aggregation_type,
                        "source_as_of": row.source_as_of,
                        "url": row.source_url,
                        "locator": row.source_row_locator,
                        "sha": row.artifact_sha256,
                        "extractor": ADAPTER_VERSION,
                        "transform": TRANSFORM_VERSION,
                        "delta": row.source_report_delta,
                        "revision_notice": row.revision_notice,
                        "lineage": lineage_sha,
                    },
                )
                accepted_ids.append(observation_id)
                if existing is None:
                    added += 1
                else:
                    revised += 1
            current_rows = (
                connection.execute(
                    text(
                        "SELECT o.id,o.record_fingerprint,o.period_start,o.period_end,o.metric_code,o.value_numeric,o.revision,o.dimensions_sha256 FROM epidemic.normalized_observations o JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id WHERE l.source_contract_id=:contract AND o.country_alpha2=:country AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id) ORDER BY o.period_start,o.period_end,o.metric_code,o.series_fingerprint,o.revision"
                    ),
                    {"contract": contract_id, "country": rows[0].country_alpha2},
                )
                .mappings()
                .all()
            )
            snapshot_material = [
                {
                    key: str(item[key])
                    for key in (
                        "record_fingerprint",
                        "period_start",
                        "period_end",
                        "metric_code",
                        "value_numeric",
                        "revision",
                        "dimensions_sha256",
                    )
                }
                for item in current_rows
            ]
            snapshot_sha = sha(snapshot_material)
            existing_snapshot = connection.execute(
                text(
                    "SELECT s.id FROM epidemic.dataset_snapshots s JOIN epidemic_p1_12.trusted_snapshot_lineage l ON l.snapshot_id=s.id WHERE l.source_contract_id=:contract AND s.query_request_id=:query AND s.content_sha256=:sha"
                ),
                {"contract": contract_id, "query": query_id, "sha": snapshot_sha},
            ).scalar_one_or_none()
            snapshot_created = existing_snapshot is None
            snapshot_id = (
                uuid.UUID(str(existing_snapshot))
                if existing_snapshot
                else stable_uuid("snapshot", f"{query_id}:{snapshot_sha}")
            )
            if snapshot_created:
                feature_spec = {
                    "contract_version": str(contract["contract_version"]),
                    "normalization_contract_version": TRANSFORM_VERSION,
                    "source_key": source_key,
                    "attribution": f"Source: {contract['publisher']}",
                    "terms_url": str(contract["terms_url"]),
                    "trust_tier": str(contract["trust_tier"]),
                    "missing_interpolated": False,
                }
                connection.execute(
                    text(
                        "INSERT INTO epidemic.dataset_snapshots(id,query_request_id,snapshot_schema_version,cutoff_at,row_count,content_sha256,feature_spec) VALUES(:id,:query,:version,:now,:count,:sha,CAST(:features AS jsonb))"
                    ),
                    {
                        "id": snapshot_id,
                        "query": query_id,
                        "version": SNAPSHOT_SCHEMA,
                        "now": now,
                        "count": len(current_rows),
                        "sha": snapshot_sha,
                        "features": canonical(feature_spec),
                    },
                )
                for number, item in enumerate(current_rows, 1):
                    connection.execute(
                        text(
                            "INSERT INTO epidemic.dataset_snapshot_observations(snapshot_id,observation_id,row_number) VALUES(:snapshot,:observation,:number)"
                        ),
                        {"snapshot": snapshot_id, "observation": item["id"], "number": number},
                    )
                date_min = min(item["period_start"] for item in current_rows)
                date_max = max(item["period_end"] for item in current_rows)
                connection.execute(
                    text(
                        "INSERT INTO epidemic_p1_12.trusted_snapshot_lineage(snapshot_id,source_contract_id,snapshot_sha256,row_count,date_min,date_max,independently_recomputed_sha256) VALUES(:snapshot,:contract,:sha,:count,:date_min,:date_max,:sha)"
                    ),
                    {
                        "snapshot": snapshot_id,
                        "contract": contract_id,
                        "sha": snapshot_sha,
                        "count": len(current_rows),
                        "date_min": date_min,
                        "date_max": date_max,
                    },
                )
            else:
                date_min = min(item["period_start"] for item in current_rows)
                date_max = max(item["period_end"] for item in current_rows)
            primary_result = results[0]
            primary_fetch = fetch_map[primary_result.sha256][0]
            normalization_key = sha(
                {"retrieval": str(retrieval_id), "manifest": manifest_sha, "snapshot": snapshot_sha}
            )
            normalization_id = stable_uuid("normalization", normalization_key)
            connection.execute(
                text(
                    "INSERT INTO epidemic.normalization_runs(id,retrieval_run_id,source_fetch_id,normalization_key,transform_version,country_mapping_version,quality_ruleset_version,snapshot_schema_version,status,result_code,attempt_number,max_attempts,created_at,started_at,finished_at,dataset_snapshot_id,total_source_rows,selected_source_rows,normalized_observation_count,missing_value_count,warning_count,unmapped_source_code_count,exact_duplicate_count) VALUES(:id,:retrieval,:fetch,:key,:transform,:mapping,:rules,:schema,'succeeded',:result,1,3,:now,:now,:now,:snapshot,:rows,:rows,:count,0,:warnings,0,0) ON CONFLICT(normalization_key) DO NOTHING"
                ),
                {
                    "id": normalization_id,
                    "retrieval": retrieval_id,
                    "fetch": primary_fetch,
                    "key": normalization_key,
                    "transform": TRANSFORM_VERSION,
                    "mapping": COUNTRY_MAPPING,
                    "rules": QUALITY_RULESET,
                    "schema": SNAPSHOT_SCHEMA,
                    "result": "PUBLISHED" if snapshot_created else "NO_CHANGE",
                    "now": now,
                    "snapshot": snapshot_id,
                    "rows": len(rows),
                    "count": len(current_rows),
                    "warnings": sum(1 for row in rows if row.revision_notice),
                },
            )
            observed_periods = len({row.period_start for row in rows})
            connection.execute(
                text(
                    "INSERT INTO epidemic.retrieval_window_resolutions(id,query_id,retrieval_run_id,time_window_kind,duration_iso8601,resolved_start,resolved_end,source_declared_max_date,source_data_through,cases_new_data_through,deaths_new_data_through,cases_cumulative_data_through,deaths_cumulative_data_through,source_http_last_modified,retrieved_at,cadence,observed_period_count,expected_period_count,missing_period_count,coverage_ratio,reporting_lag_days,freshness_status,cases_freshness_status,deaths_freshness_status,freshness_policy_version,source_key,source_version,adapter_version,contract_version,catalog_sha256,probe_evidence_sha256) VALUES(:id,:query,:retrieval,'latest_rolling_year','P1Y',:start,:end,:end,:end,NULL,NULL,:cases,:deaths,:modified,:now,:cadence,:observed,:observed,0,1.0,:lag,'current','current','current','p1-12-official-freshness-v1',:source,:source_version,:adapter,:contract,:catalog,:probe) ON CONFLICT(retrieval_run_id) DO NOTHING"
                ),
                {
                    "id": stable_uuid("window-resolution", str(retrieval_id)),
                    "query": query_id,
                    "retrieval": retrieval_id,
                    "start": date_min,
                    "end": date_max,
                    "cases": date_max if disease_slug == "ebola-disease" else None,
                    "deaths": date_max if disease_slug == "ebola-disease" else None,
                    "modified": primary_result.last_modified,
                    "now": now,
                    "cadence": "irregular" if disease_slug == "ebola-disease" else "week",
                    "observed": observed_periods,
                    "lag": max(0, (now.date() - date_max).days),
                    "source": source_key,
                    "source_version": manifest_sha[:16],
                    "adapter": ADAPTER_VERSION,
                    "contract": str(contract["contract_version"]),
                    "catalog": sha({"contract": contract_key}),
                    "probe": sha({"artifacts": sorted(fetch_map), "manifest": manifest_sha}),
                },
            )
        return IngestionResult(
            source_key,
            country,
            len(current_rows),
            added,
            revised,
            snapshot_id,
            snapshot_sha,
            snapshot_created,
            date_min,
            date_max,
            tuple(sorted(fetch_map)),
        )

    def ingest_ebola(self) -> IngestionResult:
        rows, results = self._ebola_rows()
        return self._persist(EBOLA_CONTRACT, rows, results)

    def ingest_influenza(self) -> IngestionResult:
        rows, results, _country = self._influenza_rows()
        return self._persist(INFLUENZA_CONTRACT, rows, results)

    def ingest_all(self) -> dict[str, IngestionResult]:
        return {"ebola": self.ingest_ebola(), "influenza": self.ingest_influenza()}


class TrustedUpdateService:
    """Daily coordinator extension for the two narrow P1-12 built-in contracts."""

    def __init__(self, engine: Engine, project_root: Path, ingest_engine: Engine) -> None:
        self.engine = engine
        self.ingestion = TrustedIngestionService(ingest_engine, project_root)

    def seed_due(self, trigger: str, now: dt.datetime | None = None) -> tuple[uuid.UUID, ...]:
        clock = now or dt.datetime.now(dt.timezone.utc)
        ids: list[uuid.UUID] = []
        with self.engine.begin() as connection:
            schedules = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_p1_12.trusted_update_schedules WHERE enabled AND (next_due_at<=:now OR :trigger='manual') ORDER BY schedule_id FOR UPDATE"
                    ),
                    {"now": clock, "trigger": trigger},
                )
                .mappings()
                .all()
            )
            for schedule in schedules:
                run_id = stable_uuid(
                    "trusted-update-run",
                    f"{schedule['schedule_id']}:{schedule['source_contract_version']}:{clock.date()}",
                )
                connection.execute(
                    text(
                        "INSERT INTO epidemic_p1_12.trusted_update_runs(id,schedule_id,source_contract_version,scheduled_utc_date,trigger_type,state) VALUES(:id,:schedule,:version,:date,:trigger,'QUEUED') ON CONFLICT(schedule_id,source_contract_version,scheduled_utc_date) DO NOTHING"
                    ),
                    {
                        "id": run_id,
                        "schedule": schedule["schedule_id"],
                        "version": schedule["source_contract_version"],
                        "date": clock.date(),
                        "trigger": trigger,
                    },
                )
                existing = connection.execute(
                    text(
                        "SELECT id FROM epidemic_p1_12.trusted_update_runs WHERE schedule_id=:schedule AND source_contract_version=:version AND scheduled_utc_date=:date"
                    ),
                    {
                        "schedule": schedule["schedule_id"],
                        "version": schedule["source_contract_version"],
                        "date": clock.date(),
                    },
                ).scalar_one()
                ids.append(uuid.UUID(str(existing)))
        return tuple(ids)

    def run_due_once(self) -> bool:
        with self.engine.begin() as connection:
            run = (
                connection.execute(
                    text(
                        "SELECT r.*,s.source_contract_id,c.source_key FROM epidemic_p1_12.trusted_update_runs r JOIN epidemic_p1_12.trusted_update_schedules s ON s.schedule_id=r.schedule_id JOIN epidemic_p1_12.trusted_source_contracts c ON c.id=s.source_contract_id WHERE r.state IN ('QUEUED','FAILED_RETRYABLE') AND (r.retry_not_before IS NULL OR r.retry_not_before<=now()) AND (r.claim_token IS NULL OR r.lease_expires_at<now()) ORDER BY r.created_at FOR UPDATE OF r SKIP LOCKED LIMIT 1"
                    )
                )
                .mappings()
                .one_or_none()
            )
            if run is None:
                return False
            token = uuid.uuid4()
            connection.execute(
                text(
                    "UPDATE epidemic_p1_12.trusted_update_runs SET state='CHECKING',claim_owner='daily-update-coordinator-v1',claim_token=:token,started_at=coalesce(started_at,now()),heartbeat_at=now(),lease_expires_at=now()+interval '180 seconds' WHERE id=:id"
                ),
                {"token": token, "id": run["id"]},
            )
        try:
            result = (
                self.ingestion.ingest_ebola()
                if run["source_key"] == EBOLA_CONTRACT
                else self.ingestion.ingest_influenza()
            )
            state = "SUCCEEDED_UPDATED" if result.snapshot_created else "SUCCEEDED_NO_CHANGE"
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE epidemic_p1_12.trusted_update_runs SET state=:state,claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,finished_at=now(),source_changed=:changed,observations_added=:added,observations_revised=:revised,snapshot_id=:snapshot,response_sha256=:sha,details=CAST(:details AS jsonb) WHERE id=:id AND claim_token=:token"
                    ),
                    {
                        "state": state,
                        "changed": result.snapshot_created,
                        "added": result.observations_added,
                        "revised": result.observations_revised,
                        "snapshot": result.snapshot_id,
                        "sha": result.snapshot_sha256,
                        "details": canonical(
                            {"country_iso3": result.country_iso3, "observation_count": result.observation_count}
                        ),
                        "id": run["id"],
                        "token": token,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE epidemic_p1_12.trusted_update_schedules SET last_checked_at=now(),last_success_at=now(),last_source_change_at=CASE WHEN :changed THEN now() ELSE last_source_change_at END,source_data_through=:through,consecutive_failures=0,next_due_at=((now() AT TIME ZONE timezone)::date+1+local_time) AT TIME ZONE timezone,updated_at=now() WHERE schedule_id=:schedule"
                    ),
                    {"changed": result.snapshot_created, "through": result.date_max, "schedule": run["schedule_id"]},
                )
                event_sha = sha({"run": str(run["id"]), "state": state, "snapshot": str(result.snapshot_id)})
                connection.execute(
                    text(
                        "INSERT INTO epidemic_p1_12.trusted_update_events(run_id,from_state,to_state,event_code,details,event_sha256) VALUES(:run,'CHECKING',:state,:code,CAST(:details AS jsonb),:sha) ON CONFLICT(event_sha256) DO NOTHING"
                    ),
                    {
                        "run": run["id"],
                        "state": state,
                        "code": "TRUSTED_SOURCE_UPDATED" if result.snapshot_created else "TRUSTED_SOURCE_NO_CHANGE",
                        "details": canonical(
                            {"snapshot_id": str(result.snapshot_id), "snapshot_sha256": result.snapshot_sha256}
                        ),
                        "sha": event_sha,
                    },
                )
            return True
        except Exception as exc:
            retryable = str(exc) not in {"WHO_EVENT_INDEX_DON_SERIES_DRIFT", "WHO_ODATA_SHAPE_DRIFT"}
            with self.engine.begin() as connection:
                current = connection.execute(
                    text("SELECT attempt FROM epidemic_p1_12.trusted_update_runs WHERE id=:id FOR UPDATE"),
                    {"id": run["id"]},
                ).scalar_one()
                can_retry = retryable and int(current) < 3
                state = (
                    "FAILED_RETRYABLE"
                    if can_retry
                    else ("DRIFT_REVIEW_REQUIRED" if not retryable else "FAILED_TERMINAL")
                )
                connection.execute(
                    text(
                        "UPDATE epidemic_p1_12.trusted_update_runs SET state=:state,attempt=attempt+CASE WHEN :retry THEN 1 ELSE 0 END,claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,retry_not_before=CASE WHEN :retry THEN now()+interval '30 seconds' ELSE NULL END,finished_at=CASE WHEN :retry THEN NULL ELSE now() END,failure_code=:code WHERE id=:id"
                    ),
                    {"state": state, "retry": can_retry, "code": str(exc)[:120], "id": run["id"]},
                )
                connection.execute(
                    text(
                        "UPDATE epidemic_p1_12.trusted_update_schedules SET last_checked_at=now(),consecutive_failures=consecutive_failures+1,updated_at=now() WHERE schedule_id=:schedule"
                    ),
                    {"schedule": run["schedule_id"]},
                )
            return True
