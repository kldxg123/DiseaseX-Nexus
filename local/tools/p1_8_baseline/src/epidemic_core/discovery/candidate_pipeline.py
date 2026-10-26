from __future__ import annotations

import hashlib
import json
import os
import re
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


def _mapping(headers: list[str], requested_metrics: tuple[str, ...]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for header in headers:
        key = re.sub(r"[^a-z0-9]+", "_", header.casefold()).strip("_")
        spec: dict[str, object] | None = None
        if any(token in key for token in ("date", "period_start", "week_start")):
            spec = {"canonical": "period_start", "transform": "parse_iso_date", "confidence": 0.75}
        elif any(token in key for token in ("epiweek", "iso_week", "week")):
            spec = {"canonical": "reporting_period", "transform": "parse_isoweek", "confidence": 0.7}
        elif any(token in key for token in ("country", "location", "geocode", "area_code")):
            spec = {"canonical": "location_code", "transform": "identity", "confidence": 0.65}
        elif "death" in key and "deaths_new" in requested_metrics:
            spec = {"canonical": "value", "metric": "deaths_new", "transform": "numeric_count", "confidence": 0.8}
        elif any(token in key for token in ("case", "cases")) and "cases_new" in requested_metrics:
            spec = {"canonical": "value", "metric": "cases_new", "transform": "numeric_count", "confidence": 0.8}
        elif "incidence" in key and "incidence_rate" in requested_metrics:
            spec = {"canonical": "value", "metric": "incidence_rate", "transform": "numeric_rate", "confidence": 0.8}
        if spec is not None:
            result[header] = spec
    return result


def _safe_row(row: dict[str, object]) -> dict[str, object]:
    return {
        key: "[REDACTED]" if re.search(r"email|phone|patient|person|name|cpf|address", key, re.I) else value
        for key, value in row.items()
    }


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
