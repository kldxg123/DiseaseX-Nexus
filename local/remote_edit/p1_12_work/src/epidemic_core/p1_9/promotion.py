from __future__ import annotations

# mypy: ignore-errors
import datetime as dt
import hashlib
import json
import os
import signal
import socket
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

DEMO_ENV = "p1_9_demo"
PRODUCTION_ENV = "production"
LEASE_SECONDS = 60
WORKER_VERSION = "p1-9-reviewed-promotion-v1"


class PromotionValidationError(RuntimeError):
    """A terminal contract or evidence validation error."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json(value: object) -> str:
    return canonical_json(value)


@dataclass(frozen=True)
class EnvironmentGate:
    environment: str
    database_name: str
    quarantine_root: Path
    raw_root: Path

    @classmethod
    def verify(cls, engine: Engine) -> EnvironmentGate:
        environment = os.environ.get("APP_ENV", "")
        if environment not in {DEMO_ENV, PRODUCTION_ENV}:
            raise RuntimeError("APP_ENV_INVALID")
        quarantine = Path(os.environ["DISCOVERY_QUARANTINE_ROOT"]).resolve()
        raw = Path(os.environ["PROMOTION_RAW_ROOT"]).resolve()
        with engine.connect() as connection:
            database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
            marker = (
                connection.execute(
                    text(
                        "SELECT disposable,production_write_forbidden,created_for,database_name "
                        "FROM epidemic_discovery.p1_9_environment_markers "
                        "WHERE environment_type='p1_9_demo'"
                    )
                )
                .mappings()
                .one_or_none()
            )
        if environment == DEMO_ENV:
            if "p1_9_demo" not in database_name:
                raise RuntimeError("DEMO_DATABASE_NAME_INVALID")
            if marker is None or not marker["disposable"] or not marker["production_write_forbidden"]:
                raise RuntimeError("DEMO_DATABASE_MARKER_MISSING")
            if marker["created_for"] != "P1-9" or marker["database_name"] != database_name:
                raise RuntimeError("DEMO_DATABASE_MARKER_MISMATCH")
            if "p1_9_demo" not in str(quarantine) or "p1_9_demo" not in str(raw):
                raise RuntimeError("DEMO_STORAGE_NOT_ISOLATED")
        elif marker is not None or "p1_9_demo" in database_name:
            raise RuntimeError("PRODUCTION_ENVIRONMENT_MISMATCH")
        if quarantine == raw:
            raise RuntimeError("RAW_AND_QUARANTINE_MUST_DIFFER")
        quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw.mkdir(parents=True, exist_ok=True, mode=0o700)
        return cls(environment, database_name, quarantine, raw)


def _event(
    session: Session,
    run_id: uuid.UUID,
    old: str | None,
    new: str,
    code: str,
    details: dict[str, object] | None = None,
) -> None:
    safe_details = details or {}
    event_sha = sha(
        {
            "promotion_run_id": str(run_id),
            "from": old,
            "to": new,
            "code": code,
            "details": safe_details,
            "nonce": str(uuid.uuid4()),
        }
    )
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_promotion_events"
            "(id,promotion_run_id,from_state,to_state,event_code,details,event_sha256) "
            "VALUES(:id,:run,:old,:new,:code,CAST(:details AS jsonb),:event_sha)"
        ),
        {
            "id": uuid.uuid4(),
            "run": run_id,
            "old": old,
            "new": new,
            "code": code,
            "details": _json(safe_details),
            "event_sha": event_sha,
        },
    )


def claim_one(engine: Engine, gate: EnvironmentGate, owner: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    now = dt.datetime.now(dt.timezone.utc)
    lease = now + dt.timedelta(seconds=LEASE_SECONDS)
    with Session(engine) as session, session.begin():
        row = (
            session.execute(
                text(
                    "SELECT id,state FROM epidemic_discovery.source_promotion_runs "
                    "WHERE environment=:environment AND attempt_count<max_attempts AND ("
                    "state='PROMOTION_QUEUED' OR "
                    "(state='FAILED_RETRYABLE' AND COALESCE(next_attempt_at,now())<=now()) OR "
                    "(state='CLAIMED' AND lease_expires_at<now())) "
                    "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                ),
                {"environment": gate.environment},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        token = uuid.uuid4()
        session.execute(
            text(
                "UPDATE epidemic_discovery.source_promotion_runs SET state='CLAIMED',"
                "state_version=state_version+1,claim_token=:token,claim_owner=:owner,"
                "lease_expires_at=:lease,heartbeat_at=:now,attempt_count=attempt_count+1,"
                "last_error_code=NULL,updated_at=:now WHERE id=:id"
            ),
            {"id": row["id"], "token": token, "owner": owner, "lease": lease, "now": now},
        )
        _event(session, row["id"], row["state"], "CLAIMED", "PROMOTION_CLAIMED", {"lease_seconds": LEASE_SECONDS})
        return row["id"], token


def _transition(
    engine: Engine,
    run_id: uuid.UUID,
    token: uuid.UUID,
    new_state: str,
    code: str,
    details: dict[str, object] | None = None,
) -> None:
    with Session(engine) as session, session.begin():
        old = session.execute(
            text(
                "SELECT state FROM epidemic_discovery.source_promotion_runs "
                "WHERE id=:id AND claim_token=:token FOR UPDATE"
            ),
            {"id": run_id, "token": token},
        ).scalar_one_or_none()
        if old is None:
            raise RuntimeError("STALE_CLAIM")
        updated = session.execute(
            text(
                "UPDATE epidemic_discovery.source_promotion_runs SET state=:state,"
                "state_version=state_version+1,heartbeat_at=now(),"
                "lease_expires_at=now()+(:lease||' seconds')::interval,updated_at=now() "
                "WHERE id=:id AND claim_token=:token"
            ),
            {"id": run_id, "token": token, "state": new_state, "lease": str(LEASE_SECONDS)},
        )
        if updated.rowcount != 1:
            raise RuntimeError("STALE_CLAIM")
        _event(session, run_id, old, new_state, code, details)


def _candidate(engine: Engine, run_id: uuid.UUID, token: uuid.UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """SELECT p.id promotion_id,p.environment,p.attempt_count,p.claim_token,
                d.id decision_id,d.decision,d.environment decision_environment,d.reviewer_kind,
                d.test_only,d.production_usable,d.binding_sha256,d.evidence_json,d.license_evidence_sha256,
                d.geographic_scope_decision,d.metric_decision,d.invalidated_at,
                rc.id review_case_id,rc.state review_state,rc.current_mapping_revision,
                cv.id candidate_version_id,cv.candidate_id,cv.canonical_url_redacted,cv.content_sha256,
                cv.terms_sha256,cv.license_status,cv.metadata,sc.discovery_run_id,sc.publisher,
                sc.dataset_name,sc.risk_flags,qa.assessment_sha256,qa.security_json,qa.pii_status,
                qa.license_status assessed_license_status,sp.schema_sha256,sp.profile_json,
                mp.mapping_sha256,mp.proposal_json,ar.sha256 artifact_sha256,
                ar.security_report_sha256,ar.storage_key,ar.byte_size,ar.mime_type
                FROM epidemic_discovery.source_promotion_runs p
                JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id
                JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id
                JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
                JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
                JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
                JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
                JOIN epidemic_discovery.source_mapping_proposals mp
                  ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
                JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
                WHERE p.id=:id AND p.claim_token=:token"""
                ),
                {"id": run_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RuntimeError("STALE_CLAIM")
    return dict(row)


def _expected_binding(row: dict[str, Any]) -> dict[str, str]:
    return {
        "candidate_id": str(row["candidate_id"]),
        "candidate_version_id": str(row["candidate_version_id"]),
        "discovery_run_id": str(row["discovery_run_id"]),
        "review_case_id": str(row["review_case_id"]),
        "artifact_sha256": str(row["artifact_sha256"]),
        "schema_sha256": str(row["schema_sha256"]),
        "mapping_sha256": str(row["mapping_sha256"]),
        "license_evidence_sha256": str(row["terms_sha256"]),
    }


def _validate(row: dict[str, Any], gate: EnvironmentGate) -> dict[str, Any]:
    if row["decision"] != "APPROVED" or row["review_state"] != "APPROVED":
        raise PromotionValidationError("VALID_APPROVAL_REQUIRED")
    if row["invalidated_at"] is not None:
        raise PromotionValidationError("APPROVAL_INVALIDATED")
    if row["environment"] != gate.environment or row["decision_environment"] != gate.environment:
        raise PromotionValidationError("PROMOTION_ENVIRONMENT_MISMATCH")
    if gate.environment == PRODUCTION_ENV and (row["test_only"] or not row["production_usable"]):
        raise PromotionValidationError("TEST_APPROVAL_PRODUCTION_FORBIDDEN")
    if gate.environment == DEMO_ENV and (not row["test_only"] or row["production_usable"]):
        raise PromotionValidationError("DEMO_APPROVAL_MARKER_REQUIRED")
    binding = row["evidence_json"]
    expected = _expected_binding(row)
    if binding != expected or sha(binding) != row["binding_sha256"]:
        raise PromotionValidationError("APPROVAL_BINDING_MISMATCH")
    metric = row["metric_decision"] or {}
    required_metric = {
        "metric_code": "notification_record_count",
        "unit": "notifications",
        "data_nature": "derived_aggregation",
        "training_eligible": False,
        "national_aggregate": False,
        "source_date_semantics": "notification_date",
    }
    if any(metric.get(key) != value for key, value in required_metric.items()):
        raise PromotionValidationError("MAPPING_REVALIDATION_FAILED")
    geography = row["geographic_scope_decision"] or {}
    if geography.get("national_aggregate") is not False:
        raise PromotionValidationError("NATIONAL_SCOPE_FORBIDDEN")
    if geography.get("geography_semantics") != "notification_location":
        raise PromotionValidationError("GEOGRAPHY_SCOPE_UNKNOWN")
    if gate.environment == PRODUCTION_ENV and "REVIEW_REQUIRED" in str(row["assessed_license_status"]):
        raise PromotionValidationError("LICENSE_NOT_VERIFIED")
    flags = row["risk_flags"] or []
    if any(str(flag) in {"DIRECT_IDENTIFIER", "SECRET_OR_CREDENTIAL_DETECTED"} for flag in flags):
        raise PromotionValidationError("BLOCKED_CANDIDATE_SENSITIVE_DATA")
    return expected


def _artifact_payload(row: dict[str, Any], gate: EnvironmentGate) -> bytes:
    storage_key = str(row["storage_key"])
    if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
        raise PromotionValidationError("ARTIFACT_STORAGE_KEY_INVALID")
    artifact = (gate.quarantine_root / storage_key[:2] / storage_key).resolve()
    if gate.quarantine_root not in artifact.parents:
        raise PromotionValidationError("ARTIFACT_PATH_ESCAPE")
    payload = artifact.read_bytes()
    if len(payload) != int(row["byte_size"]) or sha(payload) != row["artifact_sha256"]:
        raise PromotionValidationError("ARTIFACT_HASH_MISMATCH")
    return payload


def _record_list(document: object) -> list[dict[str, Any]]:
    if isinstance(document, list) and all(isinstance(item, dict) for item in document):
        return list(document)
    if isinstance(document, dict):
        for key in ("records", "items", "results", "result", "data", "value", "parametros"):
            value = document.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return list(value)
    raise PromotionValidationError("UNSUPPORTED_ARTIFACT_SHAPE")


def aggregate_notifications(payload: bytes) -> list[dict[str, Any]]:
    try:
        records = _record_list(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionValidationError("ARTIFACT_JSON_INVALID") from exc
    groups: dict[tuple[dt.date, str, str], set[str]] = defaultdict(set)
    for source_row in records:
        lower = {str(key).lower(): value for key, value in source_row.items()}
        raw_date = str(lower.get("dt_notific") or "")[:10]
        state_code = str(lower.get("sg_uf_not") or "").strip()
        municipality_code = str(lower.get("id_municip") or "").strip()
        if not raw_date or not state_code or not municipality_code:
            continue
        try:
            period = dt.date.fromisoformat(raw_date)
        except ValueError as exc:
            raise PromotionValidationError("NOTIFICATION_DATE_INVALID") from exc
        row_hash = sha(source_row)
        groups[(period, state_code[:20], municipality_code[:40])].add(row_hash)
    if not groups:
        raise PromotionValidationError("NO_SAFE_NOTIFICATION_ROWS")
    result: list[dict[str, Any]] = []
    for row_number, ((period, state_code, municipality_code), row_hashes) in enumerate(sorted(groups.items()), 1):
        geography = {
            "country": "Brazil",
            "state_code": state_code,
            "municipality_code": municipality_code,
            "geography_semantics": "notification_location",
            "name_status": "requires_review",
            "national_aggregate": False,
        }
        row_set_sha = sha(sorted(row_hashes))
        record = {
            "period_start": period.isoformat(),
            "geography": geography,
            "metric_code": "notification_record_count",
            "value_numeric": len(row_hashes),
            "source_row_set_sha256": row_set_sha,
        }
        result.append(
            {
                **record,
                "row_number": row_number,
                "period": period,
                "source_row_count": len(row_hashes),
                "record_fingerprint": sha(record),
            }
        )
    return result


def _stage(engine: Engine, run_id: uuid.UUID, token: uuid.UUID, rows: list[dict[str, Any]]) -> None:
    with Session(engine) as session, session.begin():
        active = session.execute(
            text(
                "SELECT 1 FROM epidemic_discovery.source_promotion_runs WHERE id=:id AND claim_token=:token FOR UPDATE"
            ),
            {"id": run_id, "token": token},
        ).scalar_one_or_none()
        if active is None:
            raise RuntimeError("STALE_CLAIM")
        session.execute(
            text("DELETE FROM epidemic_discovery.p1_9_staging_observations WHERE promotion_run_id=:id"),
            {"id": run_id},
        )
        for item in rows:
            session.execute(
                text(
                    "INSERT INTO epidemic_discovery.p1_9_staging_observations"
                    "(id,promotion_run_id,row_number,period_start,period_end,geography_json,metric_code,"
                    "value_numeric,source_row_set_sha256,record_fingerprint,quality_status) "
                    "VALUES(:id,:run,:row,:period,:period,CAST(:geography AS jsonb),"
                    "'notification_record_count',:value,:row_set,:fingerprint,'requires_review')"
                ),
                {
                    "id": uuid.uuid4(),
                    "run": run_id,
                    "row": item["row_number"],
                    "period": item["period"],
                    "geography": _json(item["geography"]),
                    "value": item["value_numeric"],
                    "row_set": item["source_row_set_sha256"],
                    "fingerprint": item["record_fingerprint"],
                },
            )


def _copy_raw(payload: bytes, artifact_sha: str, gate: EnvironmentGate) -> Path:
    target = gate.raw_root / artifact_sha[:2] / artifact_sha
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() and sha(target.read_bytes()) != artifact_sha:
        raise PromotionValidationError("RAW_ARTIFACT_COLLISION")
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.chmod(0o600)
        temporary.replace(target)
    target.chmod(0o600)
    return target


def _uuid(namespace: str, value: object) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"epidemic-openclaw:{namespace}:{value}")


def _publish_atomic(
    engine: Engine,
    run_id: uuid.UUID,
    token: uuid.UUID,
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    raw_path: Path,
) -> dict[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    metadata = row["metadata"] or {}
    source_id = _uuid("p1-9-source", row["candidate_version_id"])
    source_version_id = _uuid("p1-9-source-version", row["artifact_sha256"])
    mapping_id = _uuid("p1-9-mapping", row["mapping_sha256"])
    artifact_id = _uuid("p1-9-artifact", row["artifact_sha256"])
    retrieval_id = _uuid("p1-9-retrieval", run_id)
    fetch_id = _uuid("p1-9-fetch", run_id)
    normalization_id = _uuid("p1-9-normalization", run_id)
    snapshot_id = _uuid("p1-9-snapshot", run_id)
    publication_id = _uuid("p1-9-publication", run_id)
    mapping_contract = {
        "metric_code": "notification_record_count",
        "metric_display_name": "登革热通知记录数",
        "unit": "notifications",
        "data_nature": "derived_aggregation",
        "derivation": "each unique notification record counts once and is grouped by notification date and location",
        "training_eligible": False,
        "national_aggregate": False,
        "source_date_semantics": "notification_date",
        "geography_semantics": "notification_location",
        "quality_status": "requires_review",
        "whitelist": ["dt_notific", "sg_uf_not", "id_municip", "nu_ano"],
    }
    source_key = f"datasus-notifications-demo-{str(row['candidate_version_id'])[:12]}"
    observation_material: list[dict[str, object]] = []
    with Session(engine) as session, session.begin():
        active = session.execute(
            text(
                "SELECT state FROM epidemic_discovery.source_promotion_runs "
                "WHERE id=:id AND claim_token=:token FOR UPDATE"
            ),
            {"id": run_id, "token": token},
        ).scalar_one_or_none()
        if active is None:
            raise RuntimeError("STALE_CLAIM")
        prior = (
            session.execute(
                text(
                    "SELECT snapshot_id,data_source_id,normalization_run_id "
                    "FROM epidemic_discovery.p1_9_publications WHERE promotion_run_id=:id"
                ),
                {"id": run_id},
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            return {key: str(value) for key, value in prior.items()}
        country = session.execute(text("SELECT alpha2 FROM epidemic.countries WHERE alpha3='BRA'")).scalar_one()
        disease_id = metadata.get("disease_id")
        if not disease_id:
            disease_id = session.execute(text("SELECT id FROM epidemic.diseases WHERE slug='dengue'")).scalar_one()
        disease_id = uuid.UUID(str(disease_id))
        query_id = session.execute(
            text(
                "SELECT id FROM epidemic.query_requests WHERE country_alpha2=:country "
                "AND disease_id=:disease ORDER BY created_at DESC LIMIT 1"
            ),
            {"country": country, "disease": disease_id},
        ).scalar_one()
        session.execute(
            text(
                "INSERT INTO epidemic.metric_definitions(metric_code,display_name,unit,description,"
                "cumulative,allow_negative,min_value,schema_version,active) "
                "VALUES('notification_record_count','登革热通知记录数','notifications',"
                "'P1-9 demo-only deterministic aggregation of unique official notification records',"
                "false,false,0,1,true) ON CONFLICT(metric_code) DO NOTHING"
            )
        )
        session.execute(
            text(
                "INSERT INTO epidemic_discovery.p1_9_mapping_contracts"
                "(id,candidate_version_id,mapping_revision,mapping_sha256,metric_code,metric_display_name,"
                "unit,data_nature,training_eligible,national_aggregate,source_date_semantics,"
                "geography_semantics,contract_json) "
                "VALUES(:id,:version,:revision,:sha,'notification_record_count','登革热通知记录数',"
                "'notifications','derived_aggregation',false,false,'notification_date',"
                "'notification_location',CAST(:contract AS jsonb))"
            ),
            {
                "id": mapping_id,
                "version": row["candidate_version_id"],
                "revision": row["current_mapping_revision"],
                "sha": sha(mapping_contract),
                "contract": _json(mapping_contract),
            },
        )
        session.execute(
            text(
                "INSERT INTO epidemic.data_sources"
                "(id,source_key,display_name,source_type,authority,base_url,adapter_name,"
                "license_expression,terms_url,enabled) "
                "VALUES(:id,:key,:name,'official-public-health',:authority,:url,:adapter,"
                "'CC_ATTRIBUTION_REVIEW_REQUIRED',NULL,true)"
            ),
            {
                "id": source_id,
                "key": source_key,
                "name": str(row["dataset_name"])[:200],
                "authority": str(row["publisher"])[:200],
                "url": row["canonical_url_redacted"],
                "adapter": WORKER_VERSION,
            },
        )
        quality_profile_sha = sha(
            {"status": "requires_review", "training_eligible": False, "pii_policy": "whitelist-aggregation-v1"}
        )
        version_sha = sha(
            {
                "candidate_version": str(row["candidate_version_id"]),
                "artifact": row["artifact_sha256"],
                "mapping": sha(mapping_contract),
            }
        )
        session.execute(
            text(
                "INSERT INTO epidemic_discovery.p1_9_source_versions"
                "(id,data_source_id,candidate_version_id,adapter_version,mapping_contract_id,"
                "quality_profile_sha256,artifact_sha256,license_status,version_sha256) "
                "VALUES(:id,:source,:candidate,:adapter,:mapping,:quality,:artifact,"
                "'CC_ATTRIBUTION_REVIEW_REQUIRED',:version_sha)"
            ),
            {
                "id": source_version_id,
                "source": source_id,
                "candidate": row["candidate_version_id"],
                "adapter": WORKER_VERSION,
                "mapping": mapping_id,
                "quality": quality_profile_sha,
                "artifact": row["artifact_sha256"],
                "version_sha": version_sha,
            },
        )
        session.execute(
            text(
                "INSERT INTO epidemic.retrieval_runs"
                "(id,run_key,query_request_id,source_id,adapter_version,status,attempt_number,"
                "created_at,started_at,finished_at) "
                "VALUES(:id,:key,:query,:source,:adapter,'succeeded',1,:now,:now,:now)"
            ),
            {
                "id": retrieval_id,
                "key": f"p1-9:{run_id}",
                "query": query_id,
                "source": source_id,
                "adapter": WORKER_VERSION,
                "now": now,
            },
        )
        session.execute(
            text(
                "INSERT INTO epidemic.raw_artifacts"
                "(id,sha256,byte_size,media_type,encoding,storage_mode,storage_uri) "
                "VALUES(:id,:sha,:size,:media,'utf-8','file',:uri)"
            ),
            {
                "id": artifact_id,
                "sha": row["artifact_sha256"],
                "size": row["byte_size"],
                "media": row["mime_type"],
                "uri": str(raw_path),
            },
        )
        session.execute(
            text(
                "INSERT INTO epidemic.source_fetches"
                "(id,retrieval_run_id,source_id,canonical_url,requested_at,fetched_at,http_status,"
                "artifact_id,attempt_number,response_content_type,response_byte_size) "
                "VALUES(:id,:run,:source,:url,:now,:now,200,:artifact,1,:media,:size)"
            ),
            {
                "id": fetch_id,
                "run": retrieval_id,
                "source": source_id,
                "url": row["canonical_url_redacted"],
                "now": now,
                "artifact": artifact_id,
                "media": row["mime_type"],
                "size": row["byte_size"],
            },
        )
        observation_ids: list[uuid.UUID] = []
        for item in rows:
            dimensions = {
                **item["geography"],
                "source_key": source_key,
                "source_version_id": str(source_version_id),
                "data_nature": "derived_aggregation",
                "training_eligible": False,
                "quality_status": "requires_review",
            }
            dimensions_sha = sha(dimensions)
            series_sha = sha(
                {
                    "source": str(source_id),
                    "country": country,
                    "disease": str(disease_id),
                    "metric": "notification_record_count",
                    "period": item["period_start"],
                    "geography": item["geography"],
                }
            )
            record_sha = sha({"series": series_sha, "revision": 1, "value": item["value_numeric"]})
            observation_id = _uuid("p1-9-observation", record_sha)
            session.execute(
                text(
                    "INSERT INTO epidemic.normalized_observations"
                    "(id,record_fingerprint,series_fingerprint,revision,source_fetch_id,country_alpha2,"
                    "disease_id,metric_code,value_numeric,raw_value,period_start,period_end,granularity,"
                    "reporting_date,as_of,dimensions,dimensions_sha256,normalization_version) "
                    "VALUES(:id,:record,:series,1,:fetch,:country,:disease,'notification_record_count',"
                    ":value,:raw,:period,:period,'day',:period,:now,CAST(:dimensions AS jsonb),:dim_sha,:version)"
                ),
                {
                    "id": observation_id,
                    "record": record_sha,
                    "series": series_sha,
                    "fetch": fetch_id,
                    "country": country,
                    "disease": disease_id,
                    "value": Decimal(item["value_numeric"]),
                    "raw": str(item["value_numeric"]),
                    "period": item["period"],
                    "now": now,
                    "dimensions": _json(dimensions),
                    "dim_sha": dimensions_sha,
                    "version": WORKER_VERSION,
                },
            )
            session.execute(
                text(
                    "INSERT INTO epidemic.quality_assessments"
                    "(id,observation_id,ruleset_version,status,score,flags) "
                    "VALUES(:id,:observation,'p1-9-demo-quality-v1','accepted',0.5000,"
                    "CAST(:flags AS jsonb))"
                ),
                {
                    "id": _uuid("p1-9-quality", observation_id),
                    "observation": observation_id,
                    "flags": _json(
                        ["license_pending_human_review", "derived_aggregation", "geography_name_requires_review"]
                    ),
                },
            )
            lineage_sha = sha(
                {
                    "observation": str(observation_id),
                    "artifact": row["artifact_sha256"],
                    "source_rows": item["source_row_set_sha256"],
                }
            )
            session.execute(
                text(
                    "INSERT INTO epidemic_discovery.p1_9_observation_lineage"
                    "(observation_id,promotion_run_id,mapping_contract_id,candidate_version_id,"
                    "artifact_sha256,source_row_set_sha256,source_row_count,lineage_sha256) "
                    "VALUES(:observation,:promotion,:mapping,:candidate,:artifact,:row_set,:count,:sha)"
                ),
                {
                    "observation": observation_id,
                    "promotion": run_id,
                    "mapping": mapping_id,
                    "candidate": row["candidate_version_id"],
                    "artifact": row["artifact_sha256"],
                    "row_set": item["source_row_set_sha256"],
                    "count": item["source_row_count"],
                    "sha": lineage_sha,
                },
            )
            observation_ids.append(observation_id)
            observation_material.append(
                {
                    "record_fingerprint": record_sha,
                    "period_start": item["period_start"],
                    "value": item["value_numeric"],
                    "dimensions_sha256": dimensions_sha,
                }
            )
        if os.environ.get("P1_9_FAULT_PHASE") == "normalization":
            raise RuntimeError("FAULT_INJECTION_NORMALIZATION")
        snapshot_sha = sha(sorted(observation_material, key=lambda item: str(item["record_fingerprint"])))
        feature_spec = {
            "environment": DEMO_ENV,
            "source_id": str(source_id),
            "source_version_id": str(source_version_id),
            "mapping_contract_id": str(mapping_id),
            "artifact_sha256": row["artifact_sha256"],
            "data_nature": "derived_aggregation",
            "training_eligible": False,
            "quality_status": "requires_review",
            "license_status": "CC_ATTRIBUTION_REVIEW_REQUIRED",
            "cross_source_aggregation": False,
        }
        session.execute(
            text(
                "INSERT INTO epidemic.dataset_snapshots"
                "(id,query_request_id,snapshot_schema_version,cutoff_at,row_count,content_sha256,feature_spec) "
                "VALUES(:id,:query,1,:now,:count,:sha,CAST(:features AS jsonb))"
            ),
            {
                "id": snapshot_id,
                "query": query_id,
                "now": now,
                "count": len(observation_ids),
                "sha": snapshot_sha,
                "features": _json(feature_spec),
            },
        )
        for index, observation_id in enumerate(observation_ids, 1):
            session.execute(
                text(
                    "INSERT INTO epidemic.dataset_snapshot_observations"
                    "(snapshot_id,observation_id,row_number) VALUES(:snapshot,:observation,:row)"
                ),
                {"snapshot": snapshot_id, "observation": observation_id, "row": index},
            )
        if os.environ.get("P1_9_FAULT_PHASE") == "snapshot":
            raise RuntimeError("FAULT_INJECTION_SNAPSHOT")
        normalization_key = sha(
            {"promotion": str(run_id), "artifact": row["artifact_sha256"], "mapping": sha(mapping_contract)}
        )
        session.execute(
            text(
                "INSERT INTO epidemic.normalization_runs"
                "(id,retrieval_run_id,source_fetch_id,normalization_key,transform_version,"
                "country_mapping_version,quality_ruleset_version,snapshot_schema_version,status,"
                "result_code,attempt_number,max_attempts,created_at,started_at,finished_at,"
                "dataset_snapshot_id,total_source_rows,selected_source_rows,"
                "normalized_observation_count,warning_count) "
                "VALUES(:id,:retrieval,:fetch,:key,:transform,'p1-9-notification-location-v1',"
                "'p1-9-demo-quality-v1',1,'succeeded','PUBLISHED',1,3,:now,:now,:now,:snapshot,"
                ":source_rows,:source_rows,:observations,:observations)"
            ),
            {
                "id": normalization_id,
                "retrieval": retrieval_id,
                "fetch": fetch_id,
                "key": normalization_key,
                "transform": WORKER_VERSION,
                "now": now,
                "snapshot": snapshot_id,
                "source_rows": sum(int(item["source_row_count"]) for item in rows),
                "observations": len(observation_ids),
            },
        )
        publication_sha = sha({"run": str(run_id), "snapshot": str(snapshot_id), "snapshot_sha": snapshot_sha})
        session.execute(
            text(
                "INSERT INTO epidemic_discovery.p1_9_publications"
                "(id,promotion_run_id,data_source_id,source_version_id,normalization_run_id,"
                "snapshot_id,dashboard_path,observation_count,publication_sha256) "
                "VALUES(:id,:promotion,:source,:version,:normalization,:snapshot,:path,:count,:sha)"
            ),
            {
                "id": publication_id,
                "promotion": run_id,
                "source": source_id,
                "version": source_version_id,
                "normalization": normalization_id,
                "snapshot": snapshot_id,
                "path": f"/publications/{publication_id}",
                "count": len(observation_ids),
                "sha": publication_sha,
            },
        )
        if os.environ.get("P1_9_FAULT_PHASE") == "publication":
            raise RuntimeError("FAULT_INJECTION_PUBLICATION")
        session.execute(
            text(
                "UPDATE epidemic_discovery.source_promotion_runs SET state='PUBLISHED',"
                "state_version=state_version+1,claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,"
                "heartbeat_at=now(),formal_data_source_id=:source,normalization_run_id=:normalization,"
                "formal_snapshot_id=:snapshot,publication_id=:publication,updated_at=now() "
                "WHERE id=:id AND claim_token=:token"
            ),
            {
                "id": run_id,
                "token": token,
                "source": source_id,
                "normalization": normalization_id,
                "snapshot": snapshot_id,
                "publication": publication_id,
            },
        )
        _event(
            session,
            run_id,
            active,
            "PUBLISHED",
            "ATOMIC_PUBLICATION_SUCCEEDED",
            {"observation_count": len(observation_ids), "snapshot_sha256": snapshot_sha},
        )
    return {
        "data_source_id": str(source_id),
        "normalization_run_id": str(normalization_id),
        "snapshot_id": str(snapshot_id),
        "publication_id": str(publication_id),
    }


def _fail(engine: Engine, run_id: uuid.UUID, token: uuid.UUID, exc: Exception) -> None:
    validation = isinstance(exc, PromotionValidationError)
    error_code = str(exc)[:100] or type(exc).__name__
    with Session(engine) as session, session.begin():
        row = (
            session.execute(
                text(
                    "SELECT state,attempt_count,max_attempts FROM epidemic_discovery.source_promotion_runs "
                    "WHERE id=:id AND claim_token=:token FOR UPDATE"
                ),
                {"id": run_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        if validation:
            state = "FAILED_VALIDATION"
        elif int(row["attempt_count"]) < int(row["max_attempts"]):
            state = "FAILED_RETRYABLE"
        else:
            state = "FAILED_TERMINAL"
        session.execute(
            text(
                "UPDATE epidemic_discovery.source_promotion_runs SET state=:state,"
                "state_version=state_version+1,claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,"
                "next_attempt_at=CASE WHEN :state='FAILED_RETRYABLE' THEN now()+interval '5 seconds' ELSE NULL END,"
                "last_error_code=:code,updated_at=now() WHERE id=:id AND claim_token=:token"
            ),
            {"id": run_id, "token": token, "state": state, "code": error_code},
        )
        _event(session, run_id, row["state"], state, "ATOMIC_PUBLICATION_ROLLED_BACK", {"error_code": error_code})


def process_claim(engine: Engine, gate: EnvironmentGate, run_id: uuid.UUID, token: uuid.UUID) -> None:
    try:
        _transition(engine, run_id, token, "VALIDATING_APPROVAL", "VALIDATION_STARTED")
        row = _candidate(engine, run_id, token)
        _validate(row, gate)
        _transition(engine, run_id, token, "VALIDATING_ARTIFACT", "APPROVAL_BINDING_VALID")
        payload = _artifact_payload(row, gate)
        _transition(engine, run_id, token, "VALIDATING_LICENSE", "ARTIFACT_HASH_VALID")
        if gate.environment == DEMO_ENV:
            _transition(
                engine,
                run_id,
                token,
                "VALIDATING_SCOPE",
                "LICENSE_PENDING_DEMO_ACKNOWLEDGED",
                {"production_usable": False},
            )
        else:
            _transition(engine, run_id, token, "VALIDATING_SCOPE", "LICENSE_EVIDENCE_VALID")
        _transition(engine, run_id, token, "VALIDATING_MAPPING", "GEOGRAPHY_SCOPE_VALID")
        rows = aggregate_notifications(payload)
        _transition(engine, run_id, token, "STAGING", "MAPPING_CONTRACT_VALID")
        _stage(engine, run_id, token, rows)
        if os.environ.get("P1_9_FAULT_PHASE") == "staging":
            raise RuntimeError("FAULT_INJECTION_STAGING")
        _transition(engine, run_id, token, "NORMALIZING", "STAGING_COMPLETE", {"row_count": len(rows)})
        _transition(engine, run_id, token, "QUALITY_CHECKING", "NORMALIZATION_VALIDATED")
        if os.environ.get("P1_9_FAULT_PHASE") == "quality":
            raise RuntimeError("FAULT_INJECTION_QUALITY")
        _transition(engine, run_id, token, "SNAPSHOTTING", "QUALITY_CHECKS_PASSED")
        raw_path = _copy_raw(payload, str(row["artifact_sha256"]), gate)
        _transition(engine, run_id, token, "PUBLISHING", "SNAPSHOT_INPUT_VALID")
        _publish_atomic(engine, run_id, token, row, rows, raw_path)
    except Exception as exc:
        _fail(engine, run_id, token, exc)


def process_one(engine: Engine, gate: EnvironmentGate, owner: str) -> bool:
    claim = claim_one(engine, gate, owner)
    if claim is None:
        return False
    process_claim(engine, gate, *claim)
    return True


def main() -> None:
    engine = create_engine(os.environ["APP_DATABASE_URL"], hide_parameters=True, pool_pre_ping=True)
    gate = EnvironmentGate.verify(engine)
    owner = f"{socket.gethostname()}:{os.getpid()}:{gate.environment}"
    stopped = False

    def halt(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, halt)
    while not stopped:
        if not process_one(engine, gate, owner):
            time.sleep(1)
    engine.dispose()


if __name__ == "__main__":
    main()
