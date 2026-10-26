from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
import hashlib
import json
import os
import signal
import socket
import sys
import time
import urllib.parse
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

LEASE_SECONDS = 90
WHITELIST = ("dt_notific", "sg_uf_not", "id_municip", "nu_ano")
REQUIRED_MAPPING: dict[str, object] = {
    "metric_code": "notification_record_count",
    "display_name_zh": "登革热通知记录数",
    "unit": "notifications",
    "data_nature": "derived_aggregation",
    "training_eligible": False,
    "national_aggregate": False,
    "source_date_semantics": "notification_date",
    "geography_semantics": "notification_location",
    "quality_status": "requires_review",
}
REQUIRED_REVISION = "0020_p1_10r3_preview_permissions"


class PreviewValidationError(RuntimeError):
    pass


def preview_permission_readiness(engine: Engine) -> dict[str, object]:
    required = (
        ("epidemic_preview.preview_review_decisions", "SELECT"),
        ("epidemic_preview.preview_publication_jobs", "SELECT"),
        ("epidemic_preview.preview_publication_jobs", "UPDATE"),
        ("epidemic_preview.preview_publication_events", "INSERT"),
        ("epidemic_preview.preview_staging_observations", "INSERT"),
        ("epidemic_preview.preview_staging_observations", "DELETE"),
        ("epidemic_preview.preview_observations", "INSERT"),
        ("epidemic_preview.preview_quality_assessments", "INSERT"),
        ("epidemic_preview.preview_snapshots", "INSERT"),
        ("epidemic_preview.preview_snapshot_observations", "INSERT"),
        ("epidemic_preview.preview_dashboard_publications", "INSERT"),
    )
    forbidden = (
        ("epidemic.normalized_observations", "INSERT"),
        ("epidemic.dataset_snapshots", "INSERT"),
        ("epidemic.data_sources", "UPDATE"),
        ("epidemic." + "daily_update_" + "schedules", "UPDATE"),
    )
    checks: dict[str, bool] = {}
    with engine.connect() as connection:
        role, database = connection.execute(text("SELECT current_user,current_database()")).one()
        revision = connection.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
        checks["role"] = str(role) == "epidemicpreview"
        checks["migration"] = str(revision) == REQUIRED_REVISION
        checks["schema_usage"] = bool(
            connection.execute(
                text("SELECT has_schema_privilege(current_user,'epidemic_preview','USAGE')")
            ).scalar_one()
        )
        for target, privilege in required:
            checks[f"{privilege}:{target}"] = bool(
                connection.execute(
                    text("SELECT has_table_privilege(current_user,:target,:privilege)"),
                    {"target": target, "privilege": privilege},
                ).scalar_one()
            )
        checks["USAGE:preview_publication_events_id_seq"] = bool(
            connection.execute(
                text(
                    "SELECT has_sequence_privilege(current_user,"
                    "'epidemic_preview.preview_publication_events_id_seq','USAGE')"
                )
            ).scalar_one()
        )
        for target, privilege in forbidden:
            checks[f"forbidden:{privilege}:{target}"] = not bool(
                connection.execute(
                    text("SELECT has_table_privilege(current_user,:target,:privilege)"),
                    {"target": target, "privilege": privilege},
                ).scalar_one()
            )
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "ready": not failed,
        "error_code": None if not failed else "PREVIEW_SCHEMA_PERMISSION_DENIED",
        "required_revision": REQUIRED_REVISION,
        "database": str(database),
        "role": str(role),
        "checks": checks,
        "failed_checks": failed,
    }


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_uuid(namespace: str, value: object) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"epidemic-openclaw:p1-10:{namespace}:{value}")


@dataclass(frozen=True)
class PreviewGate:
    environment: str
    quarantine_root: Path

    @classmethod
    def verify(cls, engine: Engine) -> PreviewGate:
        environment = os.environ.get("APP_ENV", "production")
        if environment == "p1_9_demo":
            raise RuntimeError("P1_9_DEMO_ENVIRONMENT_FORBIDDEN")
        root = Path(os.environ["DISCOVERY_QUARANTINE_ROOT"]).resolve()
        if not root.is_dir():
            raise RuntimeError("QUARANTINE_ROOT_INVALID")
        readiness = preview_permission_readiness(engine)
        if not readiness["ready"]:
            raise RuntimeError("PREVIEW_SCHEMA_PERMISSION_DENIED")
        with engine.connect() as connection:
            marker = (
                connection.execute(
                    text(
                        "SELECT environment,production,disposable,database_name=current_database() AS database_match "
                        "FROM epidemic_preview.environment_markers WHERE environment=:environment"
                    ),
                    {"environment": environment},
                )
                .mappings()
                .one_or_none()
            )
        if marker is None or not marker["database_match"]:
            raise RuntimeError("PREVIEW_ENVIRONMENT_MARKER_INVALID")
        if environment == "production" and (not marker["production"] or marker["disposable"]):
            raise RuntimeError("PRODUCTION_PREVIEW_MARKER_INVALID")
        if environment == "p1_10_test" and (marker["production"] or not marker["disposable"]):
            raise RuntimeError("TEST_PREVIEW_MARKER_INVALID")
        return cls(environment=environment, quarantine_root=root)


def _event(
    session: Session,
    job_id: uuid.UUID,
    old: str | None,
    new: str,
    code: str,
    *,
    attempt: int,
    owner: str | None = None,
    details: dict[str, object] | None = None,
    error_code: str | None = None,
    requires_user_action: bool = False,
    next_step_zh: str | None = None,
) -> None:
    nonce = uuid.uuid4()
    payload = {
        "job": str(job_id),
        "old": old,
        "new": new,
        "code": code,
        "attempt": attempt,
        "owner": owner,
        "details": details or {},
        "error": error_code,
        "nonce": str(nonce),
    }
    session.execute(
        text(
            "INSERT INTO epidemic_preview.preview_publication_events(publication_job_id,from_state,to_state,"
            "event_code,attempt,claim_owner,requires_user_action,error_code,next_step_zh,details,event_sha256) "
            "VALUES(:job,:old,:new,:code,:attempt,:owner,:requires_user,:error,:next,CAST(:details AS jsonb),:sha)"
        ),
        {
            "job": job_id,
            "old": old,
            "new": new,
            "code": code,
            "attempt": attempt,
            "owner": owner,
            "requires_user": requires_user_action,
            "error": error_code,
            "next": next_step_zh,
            "details": canonical_json(details or {}),
            "sha": sha(payload),
        },
    )


def claim_one(engine: Engine, gate: PreviewGate, owner: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    token = uuid.uuid4()
    with Session(engine) as session, session.begin():
        row = (
            session.execute(
                text(
                    "SELECT j.id,j.state,j.attempt_count FROM epidemic_preview.preview_publication_jobs j "
                    "JOIN epidemic_preview.preview_review_decisions d ON d.id=j.preview_decision_id "
                    "WHERE d.environment=:environment AND ((j.state IN ('PREVIEW_QUEUED','FAILED_RETRYABLE') "
                    "AND (j.next_attempt_at IS NULL OR j.next_attempt_at<=now())) OR "
                    "(j.state NOT IN ('PREVIEW_PUBLISHED','PREVIEW_REVOKED','FAILED_FINAL') "
                    "AND j.lease_expires_at<now())) ORDER BY j.created_at,j.id "
                    "FOR UPDATE OF j SKIP LOCKED LIMIT 1"
                ),
                {"environment": gate.environment},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        attempt = int(row["attempt_count"]) + 1
        session.execute(
            text(
                "UPDATE epidemic_preview.preview_publication_jobs SET state='PREVIEW_CLAIMED',"
                "state_version=state_version+1,claim_owner=:owner,claim_token=:token,attempt_count=:attempt,"
                "heartbeat_at=now(),lease_expires_at=now()+(:lease||' seconds')::interval,"
                "failure_code=NULL,failure_detail_sha256=NULL,updated_at=now() WHERE id=:id"
            ),
            {"owner": owner, "token": token, "attempt": attempt, "lease": str(LEASE_SECONDS), "id": row["id"]},
        )
        _event(
            session,
            row["id"],
            str(row["state"]),
            "PREVIEW_CLAIMED",
            "PREVIEW_JOB_CLAIMED",
            attempt=attempt,
            owner=owner,
            next_step_zh="正在校验人工 Preview 授权",
        )
        return row["id"], token


def _transition(
    engine: Engine,
    job_id: uuid.UUID,
    token: uuid.UUID,
    new: str,
    code: str,
    details: dict[str, object] | None = None,
) -> None:
    with Session(engine) as session, session.begin():
        row = (
            session.execute(
                text(
                    "SELECT state,attempt_count,claim_owner FROM epidemic_preview.preview_publication_jobs "
                    "WHERE id=:id AND claim_token=:token FOR UPDATE"
                ),
                {"id": job_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RuntimeError("STALE_PREVIEW_CLAIM")
        session.execute(
            text(
                "UPDATE epidemic_preview.preview_publication_jobs SET state=:state,state_version=state_version+1,"
                "heartbeat_at=now(),lease_expires_at=now()+(:lease||' seconds')::interval,updated_at=now() "
                "WHERE id=:id AND claim_token=:token"
            ),
            {"state": new, "lease": str(LEASE_SECONDS), "id": job_id, "token": token},
        )
        _event(
            session,
            job_id,
            str(row["state"]),
            new,
            code,
            attempt=int(row["attempt_count"]),
            owner=str(row["claim_owner"]),
            details=details,
            next_step_zh={
                "PREVIEW_VALIDATING_DECISION": "校验授权与候选版本绑定",
                "PREVIEW_VALIDATING_ARTIFACT": "校验 Artifact 和 Schema SHA-256",
                "PREVIEW_VALIDATING_SCOPE": "校验内部预览范围与许可警告",
                "PREVIEW_VALIDATING_MAPPING": "校验通知记录聚合映射",
                "PREVIEW_SANITIZING": "仅读取白名单字段并计算源行哈希",
                "PREVIEW_AGGREGATING": "按通知日期和通知地点聚合",
                "PREVIEW_QUALITY_CHECKING": "检查范围、质量和总数",
                "PREVIEW_SNAPSHOTTING": "生成不可变 Preview Snapshot",
                "PREVIEW_PUBLISHING": "单事务切换候选预览 publication",
            }.get(new),
        )


def _candidate(engine: Engine, job_id: uuid.UUID, token: uuid.UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    """SELECT j.id job_id,j.state,j.attempt_count,j.claim_owner,d.id preview_decision_id,d.*,
                rc.state review_state,rc.state_version current_source_state_version,rc.current_mapping_revision,
                cv.terms_sha256 current_license_evidence_sha256,cv.license_status,cv.canonical_url_redacted,
                sc.publisher,sc.dataset_name,sc.risk_flags,
                COALESCE((SELECT l.discovery_run_id FROM epidemic_discovery.discovery_run_candidate_links l
                  WHERE l.candidate_version_id=cv.id ORDER BY l.linked_at DESC,l.id DESC LIMIT 1),
                  sc.discovery_run_id) current_discovery_run_id,
                ar.sha256 current_artifact_sha256,ar.storage_key,ar.byte_size,ar.mime_type,
                sp.schema_sha256 current_schema_sha256,sp.row_count,sp.column_count,sp.date_min,sp.date_max,
                mp.mapping_sha256 current_mapping_sha256,qa.pii_status,qa.license_status assessed_license_status
                FROM epidemic_preview.preview_publication_jobs j
                JOIN epidemic_preview.preview_review_decisions d ON d.id=j.preview_decision_id
                JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id
                JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
                JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
                JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
                JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
                JOIN epidemic_discovery.source_mapping_proposals mp
                  ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
                JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
                WHERE j.id=:id AND j.claim_token=:token"""
                ),
                {"id": job_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RuntimeError("STALE_PREVIEW_CLAIM")
    return dict(row)


def _validate(row: dict[str, Any], gate: PreviewGate) -> None:
    if row["environment"] != gate.environment:
        raise PreviewValidationError("PREVIEW_ENVIRONMENT_MISMATCH")
    if row["environment"] == "p1_9_demo":
        raise PreviewValidationError("P1_9_DEMO_APPROVAL_FORBIDDEN")
    if row["invalidated_at"] is not None or row["scope"] != "INTERNAL_REVIEW_ONLY":
        raise PreviewValidationError("VALID_PREVIEW_DECISION_REQUIRED")
    if row["production_usable"] or row["training_eligible"]:
        raise PreviewValidationError("PREVIEW_AUTOMATION_BOUNDARY_INVALID")
    if row["review_state"] != "REVIEW_READY":
        raise PreviewValidationError("CANDIDATE_NOT_REVIEW_READY")
    pairs = (
        ("artifact_sha256", "current_artifact_sha256", "ARTIFACT_SHA_DRIFT"),
        ("schema_sha256", "current_schema_sha256", "SCHEMA_SHA_DRIFT"),
        ("mapping_sha256", "current_mapping_sha256", "MAPPING_SHA_DRIFT"),
        ("license_evidence_sha256", "current_license_evidence_sha256", "LICENSE_EVIDENCE_DRIFT"),
        ("discovery_run_id", "current_discovery_run_id", "DISCOVERY_RUN_DRIFT"),
    )
    for expected, current, code in pairs:
        if str(row[expected]) != str(row[current]):
            raise PreviewValidationError(code)
    binding = row["binding_json"] or {}
    expected_binding = {
        "candidate_id": str(row["candidate_id"]),
        "candidate_version_id": str(row["candidate_version_id"]),
        "discovery_run_id": str(row["discovery_run_id"]),
        "review_case_id": str(row["review_case_id"]),
        "artifact_sha256": str(row["artifact_sha256"]),
        "schema_sha256": str(row["schema_sha256"]),
        "mapping_sha256": str(row["mapping_sha256"]),
        "license_evidence_sha256": str(row["license_evidence_sha256"]),
    }
    if binding != expected_binding or sha(binding) != row["binding_sha256"]:
        raise PreviewValidationError("PREVIEW_BINDING_MISMATCH")
    mapping = row["mapping_json"] or {}
    if any(mapping.get(key) != value for key, value in REQUIRED_MAPPING.items()):
        raise PreviewValidationError("PREVIEW_MAPPING_INVALID")
    if "PROHIBIT" in str(row["assessed_license_status"]).upper():
        raise PreviewValidationError("BLOCKED_PREVIEW_LICENSE_PROHIBITION")


def _artifact_payload(row: dict[str, Any], gate: PreviewGate) -> bytes:
    storage_key = str(row["storage_key"])
    if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
        raise PreviewValidationError("ARTIFACT_STORAGE_KEY_INVALID")
    path = (gate.quarantine_root / storage_key[:2] / storage_key).resolve()
    if gate.quarantine_root not in path.parents:
        raise PreviewValidationError("ARTIFACT_PATH_ESCAPE")
    payload = path.read_bytes()
    if len(payload) != int(row["byte_size"]) or sha(payload) != row["artifact_sha256"]:
        raise PreviewValidationError("ARTIFACT_HASH_MISMATCH")
    return payload


def _records(document: object) -> list[dict[str, Any]]:
    if isinstance(document, list) and all(isinstance(item, dict) for item in document):
        return list(document)
    if isinstance(document, dict):
        for key in ("records", "items", "results", "result", "data", "value", "parametros"):
            value = document.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return list(value)
    raise PreviewValidationError("UNSUPPORTED_ARTIFACT_SHAPE")


def aggregate_notifications(payload: bytes) -> list[dict[str, Any]]:
    try:
        records = _records(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreviewValidationError("ARTIFACT_JSON_INVALID") from exc
    groups: dict[tuple[dt.date, str, str], set[str]] = defaultdict(set)
    for source_row in records:
        lower = {str(key).lower(): value for key, value in source_row.items()}
        safe_projection = {key: lower.get(key) for key in WHITELIST}
        raw_date = str(safe_projection.get("dt_notific") or "")[:10]
        state_code = str(safe_projection.get("sg_uf_not") or "").strip()
        municipality_code = str(safe_projection.get("id_municip") or "").strip()
        if not raw_date or not state_code or not municipality_code:
            continue
        try:
            date = dt.date.fromisoformat(raw_date)
        except ValueError as exc:
            raise PreviewValidationError("NOTIFICATION_DATE_INVALID") from exc
        groups[(date, state_code[:20], municipality_code[:40])].add(sha(source_row))
    if not groups:
        raise PreviewValidationError("NO_SAFE_NOTIFICATION_ROWS")
    rows: list[dict[str, Any]] = []
    for ordinal, ((date, state_code, municipality_code), row_hashes) in enumerate(sorted(groups.items()), 1):
        source_set = sha(sorted(row_hashes))
        record = {
            "observation_date": date.isoformat(),
            "state_code": state_code,
            "municipality_code": municipality_code,
            "metric_code": "notification_record_count",
            "unit": "notifications",
            "value_numeric": len(row_hashes),
            "quality_status": "requires_review",
            "source_row_set_sha256": source_set,
            "source_row_count": len(row_hashes),
        }
        rows.append({**record, "ordinal": ordinal, "date": date, "record_fingerprint": sha(record)})
    return rows


def _stage(engine: Engine, job_id: uuid.UUID, token: uuid.UUID, rows: list[dict[str, Any]]) -> None:
    with Session(engine) as session, session.begin():
        if (
            session.execute(
                text(
                    "SELECT 1 FROM epidemic_preview.preview_publication_jobs WHERE id=:id AND claim_token=:token FOR UPDATE"
                ),
                {"id": job_id, "token": token},
            ).scalar_one_or_none()
            is None
        ):
            raise RuntimeError("STALE_PREVIEW_CLAIM")
        session.execute(
            text("DELETE FROM epidemic_preview.preview_staging_observations WHERE publication_job_id=:id"),
            {"id": job_id},
        )
        for row in rows:
            session.execute(
                text(
                    "INSERT INTO epidemic_preview.preview_staging_observations(id,publication_job_id,row_number,"
                    "observation_date,state_code,municipality_code,value_numeric,source_row_set_sha256,"
                    "source_row_count,record_fingerprint) VALUES(:row_id,:job,:ordinal,:date,:state,:municipality,"
                    ":value,:row_set,:row_count,:fingerprint)"
                ),
                {
                    "row_id": stable_uuid("staging", f"{job_id}:{row['record_fingerprint']}"),
                    "job": job_id,
                    "ordinal": row["ordinal"],
                    "date": row["date"],
                    "state": row["state_code"],
                    "municipality": row["municipality_code"],
                    "value": row["value_numeric"],
                    "row_set": row["source_row_set_sha256"],
                    "row_count": row["source_row_count"],
                    "fingerprint": row["record_fingerprint"],
                },
            )
        if os.environ.get("P1_10_FAULT_PHASE") == "staging":
            raise RuntimeError("FAULT_INJECTION_STAGING")


def _publish_atomic(
    engine: Engine,
    job_id: uuid.UUID,
    token: uuid.UUID,
    candidate: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, object]:
    contract = {**REQUIRED_MAPPING, "whitelist": list(WHITELIST), "coverage_status": "SAMPLE_PARTIAL_REVIEW_REQUIRED"}
    contract_sha = sha(contract)
    mapping_id = stable_uuid("mapping", f"{candidate['candidate_version_id']}:{contract_sha}")
    observations: list[dict[str, Any]] = []
    for row in rows:
        observation_id = stable_uuid("observation", f"{job_id}:{row['record_fingerprint']}")
        lineage = {
            "observation_id": str(observation_id),
            "job_id": str(job_id),
            "candidate_version_id": str(candidate["candidate_version_id"]),
            "artifact_sha256": str(candidate["artifact_sha256"]),
            "mapping_contract_id": str(mapping_id),
            "source_row_set_sha256": row["source_row_set_sha256"],
            "source_row_count": row["source_row_count"],
        }
        observations.append({**row, "id": observation_id, "lineage_sha256": sha(lineage)})
    snapshot_payload = [
        {
            "id": str(row["id"]),
            "date": row["observation_date"],
            "state": row["state_code"],
            "municipality": row["municipality_code"],
            "value": row["value_numeric"],
            "source_rows": row["source_row_set_sha256"],
        }
        for row in observations
    ]
    snapshot_sha = sha(snapshot_payload)
    snapshot_id = stable_uuid("snapshot", snapshot_sha)
    quality = {
        "status": "requires_review",
        "pii_status": "SAFE_AGGREGATE_PROJECTION_ONLY",
        "license_status": str(candidate["assessed_license_status"]),
        "coverage_status": "SAMPLE_PARTIAL_REVIEW_REQUIRED",
        "row_count": int(candidate["row_count"]),
        "observation_count": len(observations),
        "total_value": sum(int(row["value_numeric"]) for row in observations),
        "warnings": ["license_pending", "sample_partial", "not_national", "not_confirmed_cases"],
    }
    quality_sha = sha(quality)
    publication_id = stable_uuid("publication", f"{job_id}:{snapshot_sha}")
    url = urllib.parse.urlsplit(str(candidate["canonical_url_redacted"]))
    official_domain = (url.hostname or "unknown").lower()
    if official_domain != "apidadosabertos.saude.gov.br":
        raise PreviewValidationError("OFFICIAL_DOMAIN_REVALIDATION_FAILED")
    publication_sha = sha(
        {"job": str(job_id), "snapshot": str(snapshot_id), "snapshot_sha": snapshot_sha, "tier": "REVIEW_PREVIEW"}
    )
    fault = os.environ.get("P1_10_FAULT_PHASE")
    with Session(engine) as session, session.begin():
        job = (
            session.execute(
                text(
                    "SELECT state,attempt_count,claim_owner FROM epidemic_preview.preview_publication_jobs "
                    "WHERE id=:id AND claim_token=:token FOR UPDATE"
                ),
                {"id": job_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
        if job is None:
            raise RuntimeError("STALE_PREVIEW_CLAIM")
        session.execute(
            text(
                "INSERT INTO epidemic_preview.preview_mapping_contracts(id,publication_job_id,candidate_version_id,"
                "artifact_sha256,schema_sha256,source_mapping_sha256,contract_sha256,metric_code,display_name_zh,unit,"
                "data_nature,training_eligible,national_aggregate,source_date_semantics,geography_semantics,whitelist,contract_json) "
                "VALUES(:id,:job,:version,:artifact,:schema,:source_mapping,:contract_sha,'notification_record_count',"
                ":display,'notifications','derived_aggregation',false,false,'notification_date','notification_location',"
                "CAST(:whitelist AS jsonb),CAST(:contract AS jsonb)) ON CONFLICT(publication_job_id) DO NOTHING"
            ),
            {
                "id": mapping_id,
                "job": job_id,
                "version": candidate["candidate_version_id"],
                "artifact": candidate["artifact_sha256"],
                "schema": candidate["schema_sha256"],
                "source_mapping": candidate["mapping_sha256"],
                "contract_sha": contract_sha,
                "display": REQUIRED_MAPPING["display_name_zh"],
                "whitelist": canonical_json(list(WHITELIST)),
                "contract": canonical_json(contract),
            },
        )
        for row in observations:
            session.execute(
                text(
                    "INSERT INTO epidemic_preview.preview_observations(id,publication_job_id,mapping_contract_id,"
                    "candidate_version_id,artifact_sha256,observation_date,state_code,municipality_code,metric_code,unit,"
                    "value_numeric,quality_status,source_row_set_sha256,source_row_count,record_fingerprint,lineage_sha256) "
                    "VALUES(:id,:job,:mapping,:version,:artifact,:date,:state,:municipality,'notification_record_count',"
                    "'notifications',:value,'requires_review',:row_set,:row_count,:fingerprint,:lineage) ON CONFLICT(id) DO NOTHING"
                ),
                {
                    "id": row["id"],
                    "job": job_id,
                    "mapping": mapping_id,
                    "version": candidate["candidate_version_id"],
                    "artifact": candidate["artifact_sha256"],
                    "date": row["date"],
                    "state": row["state_code"],
                    "municipality": row["municipality_code"],
                    "value": row["value_numeric"],
                    "row_set": row["source_row_set_sha256"],
                    "row_count": row["source_row_count"],
                    "fingerprint": row["record_fingerprint"],
                    "lineage": row["lineage_sha256"],
                },
            )
        if fault == "quality":
            raise RuntimeError("FAULT_INJECTION_QUALITY")
        session.execute(
            text(
                "INSERT INTO epidemic_preview.preview_quality_assessments(id,publication_job_id,status,pii_status,"
                "license_status,coverage_status,row_count,observation_count,total_value,warning_json,assessment_sha256) "
                "VALUES(:id,:job,'requires_review',:pii,:license,:coverage,:rows,:observations,:total,CAST(:warnings AS jsonb),:sha) "
                "ON CONFLICT(publication_job_id) DO NOTHING"
            ),
            {
                "id": stable_uuid("quality", quality_sha),
                "job": job_id,
                "pii": quality["pii_status"],
                "license": quality["license_status"],
                "coverage": quality["coverage_status"],
                "rows": quality["row_count"],
                "observations": quality["observation_count"],
                "total": quality["total_value"],
                "warnings": canonical_json(quality["warnings"]),
                "sha": quality_sha,
            },
        )
        if fault == "snapshot":
            raise RuntimeError("FAULT_INJECTION_SNAPSHOT")
        session.execute(
            text(
                "INSERT INTO epidemic_preview.preview_snapshots(id,publication_job_id,mapping_contract_id,candidate_version_id,"
                "artifact_sha256,observation_count,total_value,date_min,date_max,state_count,municipality_count,snapshot_sha256) "
                "VALUES(:id,:job,:mapping,:version,:artifact,:count,:total,:date_min,:date_max,:states,:municipalities,:sha) "
                "ON CONFLICT(publication_job_id) DO NOTHING"
            ),
            {
                "id": snapshot_id,
                "job": job_id,
                "mapping": mapping_id,
                "version": candidate["candidate_version_id"],
                "artifact": candidate["artifact_sha256"],
                "count": len(observations),
                "total": quality["total_value"],
                "date_min": min(row["date"] for row in observations),
                "date_max": max(row["date"] for row in observations),
                "states": len({row["state_code"] for row in observations}),
                "municipalities": len({row["municipality_code"] for row in observations}),
                "sha": snapshot_sha,
            },
        )
        for ordinal, row in enumerate(observations, 1):
            session.execute(
                text(
                    "INSERT INTO epidemic_preview.preview_snapshot_observations(snapshot_id,observation_id,ordinal,member_sha256) "
                    "VALUES(:snapshot,:observation,:ordinal,:sha) ON CONFLICT(snapshot_id,observation_id) DO NOTHING"
                ),
                {
                    "snapshot": snapshot_id,
                    "observation": row["id"],
                    "ordinal": ordinal,
                    "sha": sha(snapshot_payload[ordinal - 1]),
                },
            )
        if fault == "publication":
            raise RuntimeError("FAULT_INJECTION_PUBLICATION")
        session.execute(
            text(
                "INSERT INTO epidemic_preview.preview_dashboard_publications(id,publication_job_id,preview_decision_id,"
                "snapshot_id,source_key,source_name,publisher,official_domain,country_iso2,disease_slug,license_status,"
                "coverage_status,publication_sha256) VALUES(:id,:job,:decision,:snapshot,:source_key,:source_name,:publisher,"
                ":domain,'BR','dengue',:license,'SAMPLE_PARTIAL_REVIEW_REQUIRED',:sha) "
                "ON CONFLICT(publication_job_id) DO NOTHING"
            ),
            {
                "id": publication_id,
                "job": job_id,
                "decision": candidate["preview_decision_id"],
                "snapshot": snapshot_id,
                "source_key": f"datasus-preview-{str(candidate['candidate_version_id'])[:12]}",
                "source_name": str(candidate["dataset_name"]),
                "publisher": str(candidate["publisher"]),
                "domain": official_domain,
                "license": str(candidate["assessed_license_status"]),
                "sha": publication_sha,
            },
        )
        session.execute(
            text(
                "UPDATE epidemic_preview.preview_publication_jobs SET state='PREVIEW_PUBLISHED',"
                "state_version=state_version+1,observation_count=:count,snapshot_id=:snapshot,publication_id=:publication,"
                "claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,heartbeat_at=now(),updated_at=now() "
                "WHERE id=:job AND claim_token=:token"
            ),
            {
                "count": len(observations),
                "snapshot": snapshot_id,
                "publication": publication_id,
                "job": job_id,
                "token": token,
            },
        )
        _event(
            session,
            job_id,
            str(job["state"]),
            "PREVIEW_PUBLISHED",
            "PREVIEW_ATOMIC_PUBLICATION_COMMITTED",
            attempt=int(job["attempt_count"]),
            owner=str(job["claim_owner"]),
            details={
                "observation_count": len(observations),
                "snapshot_sha256": snapshot_sha,
                "total_value": quality["total_value"],
            },
            next_step_zh="在 Dashboard 选择候选演示数据查看",
        )
    return {
        "publication_id": publication_id,
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_sha,
        "observation_count": len(observations),
        "total_value": quality["total_value"],
    }


def _fail(engine: Engine, job_id: uuid.UUID, token: uuid.UUID, exc: Exception) -> None:
    code = str(exc)[:120] if isinstance(exc, PreviewValidationError) else type(exc).__name__.upper()
    detail_sha = sha({"type": type(exc).__name__, "detail": str(exc)})
    with Session(engine) as session, session.begin():
        row = (
            session.execute(
                text(
                    "SELECT state,attempt_count,max_attempts,claim_owner FROM epidemic_preview.preview_publication_jobs "
                    "WHERE id=:id AND claim_token=:token FOR UPDATE"
                ),
                {"id": job_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        retryable = int(row["attempt_count"]) < int(row["max_attempts"])
        new = "FAILED_RETRYABLE" if retryable else "FAILED_FINAL"
        session.execute(
            text(
                "UPDATE epidemic_preview.preview_publication_jobs SET state=:state,state_version=state_version+1,"
                "failure_code=:code,failure_detail_sha256=:sha,claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,"
                "next_attempt_at=CASE WHEN :retry THEN now()+interval '5 seconds' ELSE NULL END,updated_at=now() WHERE id=:id"
            ),
            {
                "state": new,
                "code": code,
                "sha": detail_sha,
                "retry": retryable,
                "id": job_id,
            },
        )
        _event(
            session,
            job_id,
            str(row["state"]),
            new,
            "PREVIEW_PUBLICATION_FAILED",
            attempt=int(row["attempt_count"]),
            owner=str(row["claim_owner"]),
            error_code=code,
            requires_user_action=not retryable,
            next_step_zh="系统将自动重试" if retryable else "请复核候选证据后重新创建任务",
        )


def process_claim(engine: Engine, gate: PreviewGate, job_id: uuid.UUID, token: uuid.UUID) -> None:
    try:
        _transition(engine, job_id, token, "PREVIEW_VALIDATING_DECISION", "PREVIEW_DECISION_VALIDATION_STARTED")
        candidate = _candidate(engine, job_id, token)
        _validate(candidate, gate)
        _transition(engine, job_id, token, "PREVIEW_VALIDATING_ARTIFACT", "PREVIEW_DECISION_BINDING_VALID")
        payload = _artifact_payload(candidate, gate)
        _transition(engine, job_id, token, "PREVIEW_VALIDATING_SCOPE", "PREVIEW_ARTIFACT_HASH_VALID")
        _transition(engine, job_id, token, "PREVIEW_VALIDATING_MAPPING", "PREVIEW_SCOPE_VALID")
        _transition(engine, job_id, token, "PREVIEW_SANITIZING", "PREVIEW_MAPPING_VALID")
        rows = aggregate_notifications(payload)
        _transition(
            engine,
            job_id,
            token,
            "PREVIEW_AGGREGATING",
            "PREVIEW_SAFE_PROJECTION_COMPLETE",
            {"observation_count": len(rows)},
        )
        _stage(engine, job_id, token, rows)
        if os.environ.get("P1_10_FAULT_PHASE") == "staging":
            raise RuntimeError("FAULT_INJECTION_STAGING")
        _transition(engine, job_id, token, "PREVIEW_QUALITY_CHECKING", "PREVIEW_AGGREGATION_COMPLETE")
        _transition(engine, job_id, token, "PREVIEW_SNAPSHOTTING", "PREVIEW_QUALITY_CHECKS_STARTED")
        _transition(engine, job_id, token, "PREVIEW_PUBLISHING", "PREVIEW_SNAPSHOT_INPUT_VALID")
        _publish_atomic(engine, job_id, token, candidate, rows)
    except Exception as exc:
        _fail(engine, job_id, token, exc)


def process_one(engine: Engine, gate: PreviewGate, owner: str) -> bool:
    claim = claim_one(engine, gate, owner)
    if claim is None:
        return False
    process_claim(engine, gate, *claim)
    return True


def main() -> None:
    engine = create_engine(os.environ["PREVIEW_DATABASE_URL"], hide_parameters=True, pool_pre_ping=True)
    if "--readyz" in sys.argv[1:]:
        payload = preview_permission_readiness(engine)
        print(canonical_json(payload))
        raise SystemExit(0 if payload["ready"] else 1)
    gate = PreviewGate.verify(engine)
    owner = f"preview-worker:{socket.gethostname()}:{os.getpid()}:{gate.environment}"
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
