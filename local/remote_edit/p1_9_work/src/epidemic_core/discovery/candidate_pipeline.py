from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import unicodedata
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, text

from epidemic_core.discovery.providers import SearchRequest, SearchResult
from epidemic_core.discovery.safe_fetcher import FetchBlocked, SafeFetcher
from epidemic_core.discovery.service import _profile_isolated


@dataclass(frozen=True)
class MaterializationResult:
    candidate_id: uuid.UUID | None
    review_case_id: uuid.UUID | None
    network_request: bool
    http_status: int | None
    elapsed_ms: int | None
    response_sha256: str | None
    reason_code: str | None
    search_result_id: uuid.UUID | None = None
    fetch_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    parsed_rows: int = 0
    parsed_columns: int = 0
    preview_rows: int = 0
    schema_sha256: str | None = None
    date_min: str | None = None
    date_max: str | None = None
    nonzero_epidemic_values: int = 0


@dataclass(frozen=True)
class FieldRoles:
    time: list[str]
    geography: list[str]
    metrics: dict[str, str]


def _sha(value: object) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _redacted_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", "[controlled-query]" if parsed.query else "", "")
    )


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", ascii_value).strip("_")


def _number(value: object) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace("\u202f", "").replace(" ", "")
    if not raw or raw.casefold() in {"na", "n/a", "nan", "null", "none", "-"}:
        return None
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _date(value: object) -> dt.date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(raw[:10], pattern).date()
        except ValueError:
            pass
    match = re.search(r"(?P<year>20\d{2})\D*(?:W|S|SEMAINE)?\D*(?P<week>\d{1,2})", raw, re.I)
    if match:
        try:
            return dt.date.fromisocalendar(int(match.group("year")), int(match.group("week")), 1)
        except ValueError:
            return None
    return None


def _field_roles(headers: list[str], requested_metrics: tuple[str, ...]) -> FieldRoles:
    time_fields: list[str] = []
    geography_fields: list[str] = []
    metric_fields: dict[str, str] = {}
    for header in headers:
        key = _key(header)
        if (
            any(token in key for token in ("date", "jour", "period", "periode", "week", "semaine", "epiweek", "time"))
            or key.startswith("dt_")
            or key in {"ano", "nu_ano", "sem_not", "sem_pri"}
        ):
            time_fields.append(header)
        if any(
            token in key
            for token in (
                "country",
                "pays",
                "location",
                "lieu",
                "region",
                "departement",
                "territoire",
                "geocode",
                "area_code",
                "geo_name",
                "libelle",
                "municipio",
                "municipality",
            )
        ) or key in {"dep", "reg", "code_geo", "uf", "sg_uf", "id_municip", "id_mn_resi"}:
            geography_fields.append(header)
        if "cases_new" in requested_metrics and (
            any(
                token in key
                for token in (
                    "new_case",
                    "nouveaux_cas",
                    "nb_cas",
                    "cas_confirme",
                    "covid_case",
                    "case_notification",
                )
            )
            or key in {"cases_new", "cases", "case", "cas", "p", "positifs"}
        ):
            metric_fields.setdefault(header, "cases_new")
        if "deaths_new" in requested_metrics and (
            any(token in key for token in ("new_death", "nouveaux_deces", "deces", "death", "obit"))
            or key in {"deaths_new", "dc", "deaths"}
        ):
            metric_fields.setdefault(header, "deaths_new")
        if "incidence_rate" in requested_metrics and any(
            token in key for token in ("incidence", "taux_inc", "incid_rate")
        ):
            metric_fields.setdefault(header, "incidence_rate")
    return FieldRoles(time=time_fields, geography=geography_fields, metrics=metric_fields)


def _qualification(
    headers: list[str], rows: list[dict[str, object]], request: SearchRequest
) -> tuple[dict[str, object] | None, str | None]:
    roles = _field_roles(headers, request.desired_metrics)
    time_fields = list(roles.time)
    geography_fields = list(roles.geography)
    metric_fields = dict(roles.metrics)
    if not time_fields:
        return None, "TIME_FIELD_REQUIRED"
    if not geography_fields:
        return None, "GEOGRAPHY_FIELD_REQUIRED"
    if not metric_fields:
        return None, "EPIDEMIC_METRIC_FIELD_REQUIRED"
    dates = [parsed for row in rows for field in time_fields if (parsed := _date(row.get(field))) is not None]
    if not dates:
        return None, "PARSEABLE_TIME_VALUES_REQUIRED"
    today = dt.datetime.now(dt.timezone.utc).date()
    if request.time_window_kind == "latest_rolling_year" and (
        max(dates) < today - dt.timedelta(days=370) or min(dates) > today
    ):
        return None, "REQUEST_WINDOW_NO_OVERLAP"
    numeric = [number for row in rows for field in metric_fields if (number := _number(row.get(field))) is not None]
    if not numeric:
        return None, "NUMERIC_EPIDEMIC_VALUE_REQUIRED"
    nonzero = sum(number != 0 for number in numeric)
    if nonzero == 0:
        return None, "NONZERO_EPIDEMIC_VALUE_REQUIRED"
    geography_values = {
        str(row.get(field)).strip() for row in rows for field in geography_fields if row.get(field) not in (None, "")
    }
    return {
        "time_fields": time_fields,
        "geography_fields": geography_fields,
        "metric_fields": metric_fields,
        "date_min": min(dates).isoformat(),
        "date_max": max(dates).isoformat(),
        "unique_geographies": len(geography_values),
        "nonzero_epidemic_values": nonzero,
        "numeric_epidemic_values": len(numeric),
        "requested_window_overlap": True,
    }, None


def _format(content_type: str, url: str) -> str | None:
    path = urllib.parse.urlsplit(url).path.casefold()
    values = {
        "text/csv": "csv",
        "application/csv": "csv",
        "text/plain": "csv",
        "text/tab-separated-values": "tsv",
        "application/json": "json",
        "application/vnd.api+json": "json",
        "application/x-ndjson": "ndjson",
        "text/html": "html",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/zip": "zip",
    }
    selected = values.get(content_type)
    if selected:
        return selected
    for suffix, kind in (
        (".csv", "csv"),
        (".tsv", "tsv"),
        (".json", "json"),
        (".ndjson", "ndjson"),
        (".xlsx", "xlsx"),
        (".zip", "zip"),
        (".html", "html"),
    ):
        if path.endswith(suffix):
            return kind
    return None


def _mapping(headers: list[str], requested_metrics: tuple[str, ...]) -> dict[str, dict[str, object]]:
    roles = _field_roles(headers, requested_metrics)
    metric_fields = dict(roles.metrics)
    result: dict[str, dict[str, object]] = {}
    for header in headers:
        key = _key(header)
        spec: dict[str, object] | None = None
        if header in roles.time:
            spec = {"canonical": "period_start", "transform": "parse_iso_date", "confidence": 0.75}
        elif any(token in key for token in ("epiweek", "iso_week", "week")):
            spec = {"canonical": "reporting_period", "transform": "parse_isoweek", "confidence": 0.7}
        elif header in roles.geography:
            spec = {"canonical": "location_code", "transform": "identity", "confidence": 0.65}
        elif header in metric_fields:
            metric = metric_fields[header]
            spec = {
                "canonical": "value",
                "metric": metric,
                "transform": "numeric_rate" if metric == "incidence_rate" else "numeric_count",
                "confidence": 0.8,
            }
        if spec is not None:
            result[header] = spec
    return result


def _safe_row(row: dict[str, object]) -> dict[str, object]:
    return {
        key: "[REDACTED]" if re.search(r"email|phone|patient|person|name|cpf|address", key, re.I) else value
        for key, value in row.items()
    }


def persist_search_results(
    engine: Engine,
    run_id: uuid.UUID,
    results: list[tuple[SearchResult, str]],
) -> list[tuple[uuid.UUID, SearchResult, str]]:
    persisted: list[tuple[uuid.UUID, SearchResult, str]] = []
    with engine.begin() as connection:
        for result, disposition in results:
            item_id = uuid.uuid4()
            observed_at = result.observed_at or dt.datetime.now(dt.timezone.utc).isoformat()
            original_url = result.original_url or result.canonical_url
            query_sha = _sha({"provider": result.provider_id, "query": result.query_id})
            result_sha = result.result_sha256 or _sha(
                {
                    "provider": result.provider_id,
                    "query": result.query_id,
                    "rank": result.rank,
                    "url": result.canonical_url,
                    "title": result.title,
                }
            )
            row = connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_discovery_search_results("
                    "id,discovery_run_id,provider_id,query_id,query_sha256,rank,title,snippet,original_url,"
                    "canonical_url,canonical_url_sha256,domain,publisher,stable_dataset_id,source_tier,media_type,"
                    "landing_page_url,result_sha256,observed_at,metadata,disposition) VALUES("
                    ":id,:run,:provider,:query,:query_sha,:rank,:title,:snippet,:original,:canonical,:url_sha,"
                    ":domain,:publisher,:stable,:tier,:media,:landing,:result_sha,CAST(:observed AS timestamptz),"
                    "CAST(:metadata AS jsonb),:disposition) ON CONFLICT DO NOTHING RETURNING id"
                ),
                {
                    "id": item_id,
                    "run": run_id,
                    "provider": result.provider_id,
                    "query": result.query_id,
                    "query_sha": query_sha,
                    "rank": max(1, min(100, result.rank)),
                    "title": result.title[:500],
                    "snippet": result.description[:2000],
                    "original": original_url,
                    "canonical": result.canonical_url,
                    "url_sha": _sha(result.canonical_url),
                    "domain": urllib.parse.urlsplit(result.canonical_url).hostname or "",
                    "publisher": result.publisher[:500],
                    "stable": result.stable_dataset_id,
                    "tier": result.source_tier,
                    "media": result.media_type,
                    "landing": result.landing_page_url,
                    "result_sha": result_sha,
                    "observed": observed_at,
                    "metadata": json.dumps(result.metadata or {}, ensure_ascii=False),
                    "disposition": disposition,
                },
            ).scalar_one_or_none()
            if row is None:
                row = connection.execute(
                    text(
                        "SELECT id FROM epidemic_discovery.source_discovery_search_results WHERE "
                        "discovery_run_id=:run AND provider_id=:provider AND query_id=:query AND rank=:rank "
                        "AND canonical_url_sha256=:url_sha"
                    ),
                    {
                        "run": run_id,
                        "provider": result.provider_id,
                        "query": result.query_id,
                        "rank": max(1, min(100, result.rank)),
                        "url_sha": _sha(result.canonical_url),
                    },
                ).scalar_one()
            persisted.append((row, result, disposition))
    return persisted


def materialize_candidate(
    engine: Engine,
    run_id: uuid.UUID,
    request: SearchRequest,
    result: SearchResult,
    *,
    fetcher: SafeFetcher | None = None,
    quarantine_root: Path | None = None,
    profile_override: tuple[list[str], list[dict[str, object]], dict[str, int]] | None = None,
) -> MaterializationResult:
    selected_fetcher = fetcher or SafeFetcher()
    endpoint_sha = _sha(result.canonical_url)
    with engine.connect() as connection:
        existing = (
            connection.execute(
                text(
                    "SELECT c.id,rc.id review_case_id FROM epidemic_discovery.source_candidates c "
                    "LEFT JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id "
                    "LEFT JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id "
                    "WHERE c.discovery_run_id=:run AND c.canonical_endpoint_sha256=:endpoint "
                    "ORDER BY cv.version_no DESC NULLS LAST LIMIT 1"
                ),
                {"run": run_id, "endpoint": endpoint_sha},
            )
            .mappings()
            .first()
        )
    if existing is not None and existing["review_case_id"] is not None:
        return MaterializationResult(existing["id"], existing["review_case_id"], False, None, None, None, None)
    try:
        fetched = selected_fetcher.fetch(result.canonical_url)
    except FetchBlocked as exc:
        return MaterializationResult(
            None,
            None,
            exc.network_attempted,
            exc.http_status,
            None,
            None,
            exc.code,
        )
    if fetched.content_type not in {"text/csv", "application/csv", "text/plain"}:
        return MaterializationResult(
            None,
            None,
            True,
            fetched.http_status,
            fetched.elapsed_ms,
            fetched.response_sha256,
            "UNSUPPORTED_CANDIDATE_FORMAT",
        )
    root = quarantine_root or Path(os.environ["DISCOVERY_QUARANTINE_ROOT"])
    target = root / fetched.response_sha256[:2] / fetched.response_sha256
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fetched.payload)
    os.chmod(target, 0o600)
    try:
        headers, rows, quality = profile_override or _profile_isolated(target)
    except FetchBlocked as exc:
        return MaterializationResult(
            None,
            None,
            True,
            fetched.http_status,
            fetched.elapsed_ms,
            fetched.response_sha256,
            exc.code,
        )
    if not headers or not rows:
        return MaterializationResult(
            None,
            None,
            True,
            fetched.http_status,
            fetched.elapsed_ms,
            fetched.response_sha256,
            "EMPTY_CANDIDATE_DATASET",
        )
    endpoint_sha = _sha(fetched.canonical_url)
    with engine.begin() as connection:
        if connection.execute(
            text("SELECT 1 FROM epidemic_discovery.quarantine_artifacts WHERE sha256=:sha"),
            {"sha": fetched.response_sha256},
        ).first():
            return MaterializationResult(
                None,
                None,
                True,
                fetched.http_status,
                fetched.elapsed_ms,
                fetched.response_sha256,
                "DUPLICATE_CONTENT_ARTIFACT",
            )
        candidate_id, version_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        profile_id, proposal_id, review_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_candidates("
                "id,discovery_run_id,canonical_endpoint_sha256,publisher,dataset_name,source_tier,status,"
                "risk_flags,score,score_breakdown) VALUES(:id,:run,:endpoint,:publisher,:name,:tier,'REVIEW_READY',"
                "CAST(:flags AS jsonb),0.5,CAST(:score AS jsonb))"
            ),
            {
                "id": candidate_id,
                "run": run_id,
                "endpoint": endpoint_sha,
                "publisher": result.publisher[:500],
                "name": result.title[:500],
                "tier": result.source_tier,
                "flags": json.dumps(["LICENSE_REVIEW_REQUIRED", "GEOGRAPHIC_SCOPE_REVIEW_REQUIRED"]),
                "score": json.dumps({"machine_readable": 1.0, "human_review_required": 1.0}),
            },
        )
        metadata = {
            "country_iso3": request.country_iso3,
            "disease_name": request.disease_name_en,
            "time_window_kind": request.time_window_kind,
            "geographic_scope": "Requires human verification against source evidence",
            "update_frequency": "Requires human verification",
            "provider_description_sha256": _sha(result.description),
            "redirect_sha256": list(fetched.redirects),
        }
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_candidate_versions("
                "id,candidate_id,version_no,canonical_url_redacted,canonical_endpoint_sha256,publisher_evidence_sha256,"
                "terms_sha256,license_status,format,metadata,content_sha256) "
                "VALUES(:id,:candidate,1,:url,:endpoint,:publisher_sha,:terms_sha,:license,'CSV',"
                "CAST(:metadata AS jsonb),:content)"
            ),
            {
                "id": version_id,
                "candidate": candidate_id,
                "url": _redacted_url(fetched.canonical_url),
                "endpoint": endpoint_sha,
                "publisher_sha": _sha(result.publisher),
                "terms_sha": _sha(result.license_status),
                "license": result.license_status,
                "metadata": json.dumps(metadata),
                "content": fetched.response_sha256,
            },
        )
        security = {
            "https": "PASS",
            "ssrf": "PASS",
            "dns_rebinding": "PASS",
            "redirects": len(fetched.redirects),
            "formula_cells": quality.get("formula_cells", 0),
            "pii_hits": quality.get("pii_hits", 0),
        }
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.quarantine_artifacts("
                "id,candidate_version_id,sha256,byte_size,mime_type,storage_key,security_status,"
                "security_report_sha256,retrieved_at) "
                "VALUES(:id,:version,:sha,:size,:mime,:key,'PASS',:security,now())"
            ),
            {
                "id": artifact_id,
                "version": version_id,
                "sha": fetched.response_sha256,
                "size": len(fetched.payload),
                "mime": fetched.content_type,
                "key": fetched.response_sha256,
                "security": _sha(security),
            },
        )
        profile_value = {"headers": headers, "sample_policy": "head-tail", "null_policy": "preserve-null"}
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_schema_profiles("
                "id,candidate_version_id,schema_sha256,row_count,column_count,profile_json) "
                "VALUES(:id,:version,:sha,:rows,:columns,CAST(:profile AS jsonb))"
            ),
            {
                "id": profile_id,
                "version": version_id,
                "sha": _sha(profile_value),
                "rows": len(rows),
                "columns": len(headers),
                "profile": json.dumps(profile_value),
            },
        )
        for ordinal, header in enumerate(headers):
            values = [str(row.get(header)) for row in rows[:100] if row.get(header) not in (None, "")]
            numeric = bool(values) and all(re.fullmatch(r"-?\d+(?:\.\d+)?", value) for value in values)
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_schema_columns("
                    "id,schema_profile_id,ordinal,source_name,inferred_type,example_value,null_count,warning_flags) "
                    "VALUES(:id,:profile,:ordinal,:name,:kind,:example,:nulls,'[]'::jsonb)"
                ),
                {
                    "id": uuid.uuid4(),
                    "profile": profile_id,
                    "ordinal": ordinal,
                    "name": header[:500],
                    "kind": "number" if numeric else "string",
                    "example": values[0][:2000] if values else None,
                    "nulls": sum(row.get(header) in (None, "") for row in rows),
                },
            )
        mapping = _mapping(headers, request.desired_metrics)
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_mapping_proposals("
                "id,candidate_version_id,revision,mapping_sha256,proposal_json,status) "
                "VALUES(:id,:version,1,:sha,CAST(:proposal AS jsonb),'PROPOSED')"
            ),
            {"id": proposal_id, "version": version_id, "sha": _sha(mapping), "proposal": json.dumps(mapping)},
        )
        for source, spec in mapping.items():
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_mapping_fields("
                    "id,mapping_proposal_id,source_field,canonical_field,metric_code,transform,confidence) "
                    "VALUES(:id,:proposal,:source,:canonical,:metric,:transform,:confidence)"
                ),
                {
                    "id": uuid.uuid4(),
                    "proposal": proposal_id,
                    "source": source,
                    "canonical": spec.get("canonical"),
                    "metric": spec.get("metric"),
                    "transform": spec["transform"],
                    "confidence": spec["confidence"],
                },
            )
        samples = [("HEAD", index, row) for index, row in enumerate(rows[:25])]
        samples.extend(("TAIL", index, row) for index, row in enumerate(rows[-25:]))
        for kind, ordinal, row in samples:
            sanitized = _safe_row(row)
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_preview_rows("
                    "id,candidate_version_id,sample_kind,row_ordinal,raw_json,sanitized_json,row_sha256) "
                    "VALUES(:id,:version,:kind,:ordinal,CAST(:raw AS jsonb),CAST(:safe AS jsonb),:sha)"
                ),
                {
                    "id": uuid.uuid4(),
                    "version": version_id,
                    "kind": kind,
                    "ordinal": ordinal,
                    "raw": json.dumps(sanitized),
                    "safe": json.dumps(sanitized),
                    "sha": _sha(row),
                },
            )
        quality_value = {"quality": quality, "flags": ["LICENSE_REVIEW_REQUIRED", "SCOPE_REVIEW_REQUIRED"]}
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_quality_assessments("
                "id,candidate_version_id,assessment_sha256,status,quality_json,security_json,"
                "pii_status,license_status) "
                "VALUES(:id,:version,:sha,'REVIEW_READY',CAST(:quality AS jsonb),CAST(:security AS jsonb),"
                "'AGGREGATED',:license)"
            ),
            {
                "id": uuid.uuid4(),
                "version": version_id,
                "sha": _sha({"quality": quality_value, "security": security}),
                "quality": json.dumps(quality_value),
                "security": json.dumps(security),
                "license": result.license_status,
            },
        )
        etag = _sha({"version": str(version_id), "profile": str(profile_id), "mapping": str(proposal_id)})
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_review_cases("
                "id,candidate_version_id,state,current_mapping_revision,etag) "
                "VALUES(:id,:version,'REVIEW_READY',1,:etag)"
            ),
            {"id": review_id, "version": version_id, "etag": etag},
        )
    return MaterializationResult(
        candidate_id,
        review_id,
        True,
        fetched.http_status,
        fetched.elapsed_ms,
        fetched.response_sha256,
        None,
    )


def materialize_verified_candidate(
    engine: Engine,
    run_id: uuid.UUID,
    request: SearchRequest,
    result: SearchResult,
    search_result_id: uuid.UUID,
    *,
    fetcher: SafeFetcher | None = None,
    quarantine_root: Path | None = None,
    profile_override: tuple[list[str], list[dict[str, object]], dict[str, int]] | None = None,
) -> MaterializationResult:
    """Fetch, deterministically qualify, and materialize one ledger-backed candidate."""
    selected_fetcher = fetcher or SafeFetcher()
    fetch_id = uuid.uuid4()
    domain = urllib.parse.urlsplit(result.canonical_url).hostname or ""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.candidate_fetches("
                "id,discovery_run_id,search_result_id,domain,status) VALUES(:id,:run,:result,:domain,'STARTED')"
            ),
            {"id": fetch_id, "run": run_id, "result": search_result_id, "domain": domain},
        )

    def failed(
        reason: str,
        *,
        network: bool,
        http_status: int | None = None,
        elapsed_ms: int | None = None,
        response_sha256: str | None = None,
        final_url: str | None = None,
        content_type: str | None = None,
        byte_size: int | None = None,
    ) -> MaterializationResult:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE epidemic_discovery.candidate_fetches SET status=:status,completed_at=now(),"
                    "final_url=:url,final_url_sha256=:url_sha,http_status=:http,content_type=:content,"
                    "byte_size=:bytes,elapsed_ms=:elapsed,artifact_sha256=:artifact,failure_code=:reason "
                    "WHERE id=:id AND status='STARTED'"
                ),
                {
                    "status": "BLOCKED"
                    if "BLOCKED" in reason or reason in {"ALREADY_FORMAL_SOURCE", "DUPLICATE_CONTENT_ARTIFACT"}
                    else "FAILED",
                    "url": final_url,
                    "url_sha": _sha(final_url) if final_url else None,
                    "http": http_status,
                    "content": content_type,
                    "bytes": byte_size,
                    "elapsed": elapsed_ms,
                    "artifact": response_sha256,
                    "reason": reason,
                    "id": fetch_id,
                },
            )
        return MaterializationResult(
            None,
            None,
            network,
            http_status,
            elapsed_ms,
            response_sha256,
            reason,
            search_result_id,
            fetch_id,
        )

    try:
        fetched = selected_fetcher.fetch(result.canonical_url)
    except FetchBlocked as exc:
        return failed(exc.code, network=exc.network_attempted, http_status=exc.http_status)
    data_format = _format(fetched.content_type, fetched.canonical_url)
    if data_format is None:
        return failed(
            "UNSUPPORTED_CANDIDATE_FORMAT",
            network=True,
            http_status=fetched.http_status,
            elapsed_ms=fetched.elapsed_ms,
            response_sha256=fetched.response_sha256,
            final_url=fetched.canonical_url,
            content_type=fetched.content_type,
            byte_size=len(fetched.payload),
        )
    root = quarantine_root or Path(os.environ["DISCOVERY_QUARANTINE_ROOT"])
    target = root / fetched.response_sha256[:2] / fetched.response_sha256
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fetched.payload)
    os.chmod(target, 0o600)
    try:
        headers, rows, quality = profile_override or _profile_isolated(target, data_format)
    except FetchBlocked as exc:
        return failed(
            exc.code,
            network=True,
            http_status=fetched.http_status,
            elapsed_ms=fetched.elapsed_ms,
            response_sha256=fetched.response_sha256,
            final_url=fetched.canonical_url,
            content_type=fetched.content_type,
            byte_size=len(fetched.payload),
        )
    if result.metadata and result.metadata.get("record_semantics") == "case_notification":
        derived_field = str(result.metadata.get("derived_preview_field") or "case_notification_count")
        if derived_field not in headers:
            headers = [*headers, derived_field]
            rows = [{**row, derived_field: "1"} for row in rows]
            quality = {
                **quality,
                "derived_preview_fields": [derived_field],
                "derived_preview_rule": "one official notification record equals one notification count",
            }
    if not headers or not rows:
        return failed(
            "EMPTY_CANDIDATE_DATASET",
            network=True,
            http_status=fetched.http_status,
            elapsed_ms=fetched.elapsed_ms,
            response_sha256=fetched.response_sha256,
            final_url=fetched.canonical_url,
            content_type=fetched.content_type,
            byte_size=len(fetched.payload),
        )
    qualification, reason = _qualification(headers, rows, request)
    if qualification is None:
        return failed(
            reason or "CANDIDATE_QUALIFICATION_FAILED",
            network=True,
            http_status=fetched.http_status,
            elapsed_ms=fetched.elapsed_ms,
            response_sha256=fetched.response_sha256,
            final_url=fetched.canonical_url,
            content_type=fetched.content_type,
            byte_size=len(fetched.payload),
        )
    endpoint_sha = _sha(fetched.canonical_url)
    with engine.begin() as connection:
        if connection.execute(
            text("SELECT 1 FROM epidemic.raw_artifacts WHERE sha256=:sha"),
            {"sha": fetched.response_sha256},
        ).first():
            return failed(
                "ALREADY_FORMAL_SOURCE",
                network=True,
                http_status=fetched.http_status,
                elapsed_ms=fetched.elapsed_ms,
                response_sha256=fetched.response_sha256,
                final_url=fetched.canonical_url,
                content_type=fetched.content_type,
                byte_size=len(fetched.payload),
            )
        if connection.execute(
            text("SELECT 1 FROM epidemic_discovery.quarantine_artifacts WHERE sha256=:sha"),
            {"sha": fetched.response_sha256},
        ).first():
            return failed(
                "DUPLICATE_CONTENT_ARTIFACT",
                network=True,
                http_status=fetched.http_status,
                elapsed_ms=fetched.elapsed_ms,
                response_sha256=fetched.response_sha256,
                final_url=fetched.canonical_url,
                content_type=fetched.content_type,
                byte_size=len(fetched.payload),
            )
        candidate_id, version_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        profile_id, proposal_id, review_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        flags = ["LICENSE_REVIEW_REQUIRED", "GEOGRAPHIC_SCOPE_REVIEW_REQUIRED", "HUMAN_MAPPING_REVIEW_REQUIRED"]
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_candidates("
                "id,discovery_run_id,canonical_endpoint_sha256,publisher,dataset_name,source_tier,status,"
                "risk_flags,score,score_breakdown) VALUES(:id,:run,:endpoint,:publisher,:name,:tier,'REVIEW_READY',"
                "CAST(:flags AS jsonb),0.7,CAST(:score AS jsonb))"
            ),
            {
                "id": candidate_id,
                "run": run_id,
                "endpoint": endpoint_sha,
                "publisher": result.publisher[:500],
                "name": result.title[:500],
                "tier": result.source_tier,
                "flags": json.dumps(flags),
                "score": json.dumps({"machine_readable": 1.0, "time_overlap": 1.0, "nonzero_metric": 1.0}),
            },
        )
        metadata = {
            "country_iso3": request.country_iso3,
            "country_name": request.country_name_en,
            "disease_name": request.disease_name_en,
            "time_window_kind": request.time_window_kind,
            "geographic_scope": "Requires human verification against parsed geography fields",
            "scope_match": "UNKNOWN",
            "update_frequency": "Requires human verification",
            "provider_id": result.provider_id,
            "query_id": result.query_id,
            "search_result_id": str(search_result_id),
            "fetch_id": str(fetch_id),
            "landing_page_url": result.landing_page_url,
            "download_url": fetched.canonical_url,
            "stable_dataset_id": result.stable_dataset_id,
            "qualification": qualification,
            "provider_metadata": result.metadata or {},
            "redirect_sha256": list(fetched.redirects),
        }
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_candidate_versions("
                "id,candidate_id,version_no,canonical_url_redacted,canonical_endpoint_sha256,publisher_evidence_sha256,"
                "terms_sha256,license_status,format,metadata,content_sha256) VALUES("
                ":id,:candidate,1,:url,:endpoint,:publisher_sha,:terms_sha,:license,:format,"
                "CAST(:metadata AS jsonb),:content)"
            ),
            {
                "id": version_id,
                "candidate": candidate_id,
                "url": _redacted_url(fetched.canonical_url),
                "endpoint": endpoint_sha,
                "publisher_sha": _sha(result.publisher),
                "terms_sha": _sha(result.license_status),
                "license": result.license_status,
                "format": data_format.upper(),
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "content": fetched.response_sha256,
            },
        )
        security = {
            "https": "PASS",
            "tls_verification": "PASS",
            "ssrf": "PASS",
            "dns_rebinding": "PASS",
            "redirects": len(fetched.redirects),
            "active_content_executed": False,
            "formula_cells_executed": False,
            "formula_cells_detected": quality.get("formula_cells", 0),
            "pii_hits": quality.get("pii_hits", 0),
            "prompt_injection_hits": quality.get("prompt_injection_hits", 0),
        }
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.quarantine_artifacts("
                "id,candidate_version_id,sha256,byte_size,mime_type,storage_key,security_status,"
                "security_report_sha256,retrieved_at,search_result_id,fetch_id) VALUES("
                ":id,:version,:sha,:size,:mime,:key,'PASS',:security,now(),:search_result,:fetch)"
            ),
            {
                "id": artifact_id,
                "version": version_id,
                "sha": fetched.response_sha256,
                "size": len(fetched.payload),
                "mime": fetched.content_type,
                "key": fetched.response_sha256,
                "security": _sha(security),
                "search_result": search_result_id,
                "fetch": fetch_id,
            },
        )
        row_count = int(quality.get("rows", len(rows)))
        profile_value = {
            "headers": headers,
            "sample_policy": "first-100-deterministic",
            "null_policy": "preserve-null-distinct-from-zero",
            "qualification": qualification,
            "quality": quality,
        }
        schema_sha = _sha(profile_value)
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_schema_profiles("
                "id,candidate_version_id,schema_sha256,row_count,column_count,date_min,date_max,profile_json) "
                "VALUES(:id,:version,:sha,:rows,:columns,CAST(:date_min AS date),"
                "CAST(:date_max AS date),CAST(:profile AS jsonb))"
            ),
            {
                "id": profile_id,
                "version": version_id,
                "sha": schema_sha,
                "rows": row_count,
                "columns": len(headers),
                "date_min": qualification["date_min"],
                "date_max": qualification["date_max"],
                "profile": json.dumps(profile_value, ensure_ascii=False),
            },
        )
        for ordinal, header in enumerate(headers):
            values = [str(row.get(header)) for row in rows[:100] if row.get(header) not in (None, "")]
            numeric = bool(values) and sum(_number(value) is not None for value in values) >= max(1, len(values) // 2)
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_schema_columns("
                    "id,schema_profile_id,ordinal,source_name,inferred_type,example_value,null_count,warning_flags) "
                    "VALUES(:id,:profile,:ordinal,:name,:kind,:example,:nulls,CAST(:warnings AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "profile": profile_id,
                    "ordinal": ordinal,
                    "name": header[:500],
                    "kind": "number" if numeric else "string",
                    "example": values[0][:2000] if values else None,
                    "nulls": sum(row.get(header) in (None, "") for row in rows),
                    "warnings": json.dumps(
                        ["FORMULA_TEXT_NOT_EXECUTED"]
                        if any(value.lstrip().startswith(("=", "+", "-", "@")) for value in values)
                        else []
                    ),
                },
            )
        mapping = _mapping(headers, request.desired_metrics)
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_mapping_proposals("
                "id,candidate_version_id,revision,mapping_sha256,proposal_json,status) "
                "VALUES(:id,:version,1,:sha,CAST(:proposal AS jsonb),'PROPOSED')"
            ),
            {"id": proposal_id, "version": version_id, "sha": _sha(mapping), "proposal": json.dumps(mapping)},
        )
        for source, spec in mapping.items():
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_mapping_fields("
                    "id,mapping_proposal_id,source_field,canonical_field,metric_code,transform,confidence,warning) "
                    "VALUES(:id,:proposal,:source,:canonical,:metric,:transform,:confidence,:warning)"
                ),
                {
                    "id": uuid.uuid4(),
                    "proposal": proposal_id,
                    "source": source,
                    "canonical": spec.get("canonical"),
                    "metric": spec.get("metric"),
                    "transform": spec["transform"],
                    "confidence": spec["confidence"],
                    "warning": "SUGGESTED_MAPPING_REQUIRES_HUMAN_REVIEW",
                },
            )
        preview = rows[:100]
        for ordinal, row in enumerate(preview):
            sanitized = _safe_row(row)
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_preview_rows("
                    "id,candidate_version_id,sample_kind,row_ordinal,raw_json,sanitized_json,row_sha256) "
                    "VALUES(:id,:version,'SAMPLE',:ordinal,CAST(:raw AS jsonb),CAST(:safe AS jsonb),:sha)"
                ),
                {
                    "id": uuid.uuid4(),
                    "version": version_id,
                    "ordinal": ordinal,
                    "raw": json.dumps(sanitized, ensure_ascii=False),
                    "safe": json.dumps(sanitized, ensure_ascii=False),
                    "sha": _sha(row),
                },
            )
        quality_value = {
            "quality": quality,
            "qualification": qualification,
            "preview_row_count": len(preview),
            "flags": flags,
        }
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_quality_assessments("
                "id,candidate_version_id,assessment_sha256,status,quality_json,security_json,"
                "pii_status,license_status) "
                "VALUES(:id,:version,:sha,'REVIEW_READY',CAST(:quality AS jsonb),CAST(:security AS jsonb),"
                "'AGGREGATED',:license)"
            ),
            {
                "id": uuid.uuid4(),
                "version": version_id,
                "sha": _sha({"quality": quality_value, "security": security}),
                "quality": json.dumps(quality_value, ensure_ascii=False),
                "security": json.dumps(security),
                "license": result.license_status,
            },
        )
        etag = _sha({"version": str(version_id), "profile": str(profile_id), "mapping": str(proposal_id)})
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.source_review_cases("
                "id,candidate_version_id,state,current_mapping_revision,etag) "
                "VALUES(:id,:version,'REVIEW_READY',1,:etag)"
            ),
            {"id": review_id, "version": version_id, "etag": etag},
        )
        connection.execute(
            text(
                "UPDATE epidemic_discovery.candidate_fetches SET candidate_id=:candidate,status='REVIEW_READY',"
                "completed_at=now(),final_url=:url,final_url_sha256=:url_sha,http_status=:http,"
                "content_type=:content,byte_size=:bytes,etag=:etag,last_modified=:modified,elapsed_ms=:elapsed,"
                "artifact_sha256=:artifact,redirect_sha256=CAST(:redirects AS jsonb) WHERE id=:id AND status='STARTED'"
            ),
            {
                "candidate": candidate_id,
                "url": fetched.canonical_url,
                "url_sha": endpoint_sha,
                "http": fetched.http_status,
                "content": fetched.content_type,
                "bytes": len(fetched.payload),
                "etag": fetched.etag,
                "modified": fetched.last_modified,
                "elapsed": fetched.elapsed_ms,
                "artifact": fetched.response_sha256,
                "redirects": json.dumps(list(fetched.redirects)),
                "id": fetch_id,
            },
        )
    return MaterializationResult(
        candidate_id,
        review_id,
        True,
        fetched.http_status,
        fetched.elapsed_ms,
        fetched.response_sha256,
        None,
        search_result_id,
        fetch_id,
        artifact_id,
        row_count,
        len(headers),
        len(preview),
        schema_sha,
        str(qualification["date_min"]),
        str(qualification["date_max"]),
        int(str(qualification["nonzero_epidemic_values"])),
    )
