from __future__ import annotations
# ruff: noqa: E501
# mypy: disable-error-code="assignment,arg-type,attr-defined,index"

import csv
import datetime as dt
import decimal
import hashlib
import io
import json
import os
import signal
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from epidemic_core.discovery.service import TRANSFORMS, canonical, sha


def _event(session: Session, run_id: uuid.UUID, old: str | None, new: str, code: str, details: dict[str, object]) -> None:
    digest = sha({"run": str(run_id), "from": old, "to": new, "code": code, "details": details, "nonce": str(uuid.uuid4())})
    session.execute(
        text("INSERT INTO epidemic_discovery.source_promotion_events(id,promotion_run_id,from_state,to_state,event_code,details,event_sha256) VALUES(:id,:run,:old,:new,:code,CAST(:details AS jsonb),:sha)"),
        {"id": uuid.uuid4(), "run": run_id, "old": old, "new": new, "code": code, "details": json.dumps(details), "sha": digest},
    )


def _queue_approved(session: Session) -> bool:
    decision = session.execute(
        text("SELECT d.id,d.decision_sha256 FROM epidemic_discovery.source_review_decisions d LEFT JOIN epidemic_discovery.source_promotion_runs p ON p.review_decision_id=d.id WHERE d.decision='APPROVED' AND p.id IS NULL ORDER BY d.created_at FOR UPDATE OF d SKIP LOCKED LIMIT 1")
    ).mappings().first()
    if not decision:
        return False
    run_id = uuid.uuid4()
    key = sha({"decision": str(decision["id"]), "decision_sha256": decision["decision_sha256"]})
    session.execute(
        text("INSERT INTO epidemic_discovery.source_promotion_runs(id,review_decision_id,state,promotion_key) VALUES(:id,:decision,'PROMOTION_QUEUED',:key) ON CONFLICT(review_decision_id) DO NOTHING"),
        {"id": run_id, "decision": decision["id"], "key": key},
    )
    _event(session, run_id, None, "PROMOTION_QUEUED", "APPROVAL_ACCEPTED", {"decision_sha256": decision["decision_sha256"]})
    return True


def _load_rows(payload: bytes, mapping: dict[str, dict[str, str]]) -> list[tuple[dt.date, decimal.Decimal, dict[str, object]]]:
    if any(spec.get("transform") not in TRANSFORMS for spec in mapping.values()):
        raise RuntimeError("UNAPPROVED_TRANSFORM")
    date_fields = [key for key, spec in mapping.items() if spec.get("canonical") == "period_start"]
    value_fields = [key for key, spec in mapping.items() if spec.get("canonical") == "value" and spec.get("metric") == "cases_new"]
    if len(date_fields) != 1 or len(value_fields) != 1:
        raise RuntimeError("MAPPING_REVALIDATION_FAILED")
    result: list[tuple[dt.date, decimal.Decimal, dict[str, object]]] = []
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    for row in reader:
        raw_date, raw_value = row.get(date_fields[0]), row.get(value_fields[0])
        if raw_date in (None, "") or raw_value in (None, ""):
            continue
        period = dt.date.fromisoformat(str(raw_date)[:10])
        value = decimal.Decimal(str(raw_value))
        if not value.is_finite() or value < 0:
            raise RuntimeError("QUALITY_REVALIDATION_FAILED")
        dimensions = {"approved_discovery_source": True}
        for key, spec in mapping.items():
            if spec.get("canonical") == "location_code" and row.get(key):
                dimensions["location_code"] = str(row[key])[:100]
        result.append((period, value, dimensions))
    if not result:
        raise RuntimeError("NO_OBSERVATIONS")
    return result


def _candidate(session: Session, promotion_id: uuid.UUID) -> dict[str, object]:
    return dict(
        session.execute(
            text("""SELECT p.id promotion_id,p.state,d.id decision_id,d.evidence_json,d.evidence_sha256,d.decision_sha256,
              rc.id review_case_id,rc.state review_state,cv.id version_id,cv.candidate_id,cv.canonical_url_redacted,
              cv.canonical_endpoint_sha256,cv.publisher_evidence_sha256,cv.terms_sha256,cv.content_sha256,cv.metadata,
              sc.publisher,sc.dataset_name,sc.risk_flags,qa.assessment_sha256,qa.security_json,qa.pii_status,qa.license_status,
              sp.schema_sha256,mp.mapping_sha256,mp.proposal_json,ar.sha256 artifact_sha256,ar.security_report_sha256,ar.storage_key
              FROM epidemic_discovery.source_promotion_runs p
              JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id
              JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id
              JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
              JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
              JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
              JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
              JOIN epidemic_discovery.source_mapping_proposals mp ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
              JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
              WHERE p.id=:id FOR UPDATE OF p"""),
            {"id": promotion_id},
        ).mappings().one()
    )


def _verify(row: dict[str, object], quarantine: Path) -> tuple[bytes, list[tuple[dt.date, decimal.Decimal, dict[str, object]]]]:
    if row["review_state"] != "APPROVED" or row["pii_status"] != "AGGREGATED" or row["license_status"] in {"UNKNOWN", "RESTRICTED", "PROHIBITED"}:
        raise RuntimeError("APPROVAL_STALE")
    if row["risk_flags"] and any(flag.endswith("_BLOCKED") or flag in {"PERSON_LEVEL_DATA", "SECRET_OR_CREDENTIAL_DETECTED"} for flag in row["risk_flags"]):
        raise RuntimeError("SECURITY_REVALIDATION_FAILED")
    evidence = row["evidence_json"]
    if sha(evidence) != row["evidence_sha256"]:
        raise RuntimeError("EVIDENCE_HASH_MISMATCH")
    bindings = {
        "candidate_version_id": row["version_id"], "canonical_endpoint_sha256": row["canonical_endpoint_sha256"],
        "publisher_evidence_sha256": row["publisher_evidence_sha256"], "terms_sha256": row["terms_sha256"],
        "artifact_sha256": row["artifact_sha256"], "schema_sha256": row["schema_sha256"], "mapping_sha256": row["mapping_sha256"],
        "assessment_sha256": row["assessment_sha256"], "security_report_sha256": row["security_report_sha256"],
    }
    if any(str(evidence.get(key)) != str(value) for key, value in bindings.items()):
        raise RuntimeError("APPROVAL_STALE")
    artifact = quarantine / str(row["storage_key"])[:2] / str(row["storage_key"])
    payload = artifact.read_bytes()
    if hashlib.sha256(payload).hexdigest() != row["artifact_sha256"]:
        raise RuntimeError("ARTIFACT_HASH_MISMATCH")
    return payload, _load_rows(payload, row["proposal_json"])


def _promote(session: Session, row: dict[str, object], payload: bytes, rows: list[tuple[dt.date, decimal.Decimal, dict[str, object]]]) -> uuid.UUID:
    now = dt.datetime.now(dt.timezone.utc)
    metadata = row["metadata"]
    country = session.execute(text("SELECT alpha2 FROM epidemic.countries WHERE alpha3=:alpha3"), {"alpha3": metadata["country_iso3"]}).scalar_one()
    disease_id = uuid.UUID(str(metadata["disease_id"]))
    query_id = session.execute(text("SELECT id FROM epidemic.query_requests WHERE country_alpha2=:country AND disease_id=:disease ORDER BY created_at DESC LIMIT 1"), {"country": country, "disease": disease_id}).scalar_one()
    source_id, retrieval_id, fetch_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    normalization_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    source_key = f"approved-discovery-{str(row['candidate_id'])[:24]}"
    session.execute(text("INSERT INTO epidemic.data_sources(id,source_key,display_name,source_type,authority,base_url,adapter_name,license_expression,enabled) VALUES(:id,:key,:name,'official-public-health',:authority,:url,'approved-declarative-mapping-v1','human-reviewed-custom-terms',false)"), {"id": source_id, "key": source_key, "name": str(row["dataset_name"])[:200], "authority": str(row["publisher"])[:200], "url": row["canonical_url_redacted"]})
    run_key = f"promotion:{row['promotion_id']}"
    session.execute(text("INSERT INTO epidemic.retrieval_runs(id,run_key,query_request_id,source_id,adapter_version,status,attempt_number,started_at,finished_at) VALUES(:id,:key,:query,:source,'approved-declarative-mapping-v1','succeeded',1,:now,:now)"), {"id": retrieval_id, "key": run_key, "query": query_id, "source": source_id, "now": now})
    session.execute(text("INSERT INTO epidemic.raw_artifacts(id,sha256,byte_size,media_type,encoding,storage_mode,inline_text) VALUES(:id,:sha,:size,'text/csv','utf-8','inline_text',:payload)"), {"id": artifact_id, "sha": row["artifact_sha256"], "size": len(payload), "payload": payload.decode("utf-8-sig")})
    session.execute(text("INSERT INTO epidemic.source_fetches(id,retrieval_run_id,source_id,canonical_url,requested_at,fetched_at,http_status,artifact_id,attempt_number,response_content_type,response_byte_size) VALUES(:id,:run,:source,:url,:now,:now,200,:artifact,1,'text/csv',:size)"), {"id": fetch_id, "run": retrieval_id, "source": source_id, "url": row["canonical_url_redacted"], "now": now, "artifact": artifact_id, "size": len(payload)})
    observation_ids = []
    for period, value, dimensions in rows:
        dimensions["source_key"] = source_key
        dimensions_sha = sha(dimensions)
        series_sha = sha({"country": country, "disease": str(disease_id), "metric": "cases_new", "period": period.isoformat(), "dimensions": dimensions})
        record_sha = sha({"series": series_sha, "revision": 1, "value": str(value)})
        observation_id = uuid.uuid4()
        session.execute(text("INSERT INTO epidemic.normalized_observations(id,record_fingerprint,series_fingerprint,revision,source_fetch_id,country_alpha2,disease_id,metric_code,value_numeric,raw_value,period_start,period_end,granularity,as_of,dimensions,dimensions_sha256,normalization_version) VALUES(:id,:record,:series,1,:fetch,:country,:disease,'cases_new',:value,:raw,:start,:end,'week',:now,CAST(:dimensions AS jsonb),:dimensions_sha,'approved-declarative-mapping-v1')"), {"id": observation_id, "record": record_sha, "series": series_sha, "fetch": fetch_id, "country": country, "disease": disease_id, "value": value, "raw": str(value), "start": period, "end": period + dt.timedelta(days=6), "now": now, "dimensions": json.dumps(dimensions), "dimensions_sha": dimensions_sha})
        session.execute(text("INSERT INTO epidemic.quality_assessments(id,observation_id,ruleset_version,status,score,flags) VALUES(:id,:observation,'approved-source-quality-v1','accepted',1.0,'[]'::jsonb)"), {"id": uuid.uuid4(), "observation": observation_id})
        observation_ids.append(observation_id)
    content_sha = sha([str(value) for value in observation_ids])
    feature = {"contract_version": "approved-source-profile-v1", "attribution": row["publisher"], "terms_status": row["license_status"], "promotion_id": str(row["promotion_id"])}
    session.execute(text("INSERT INTO epidemic.dataset_snapshots(id,query_request_id,snapshot_schema_version,cutoff_at,row_count,content_sha256,feature_spec) VALUES(:id,:query,1,:now,:count,:sha,CAST(:feature AS jsonb))"), {"id": snapshot_id, "query": query_id, "now": now, "count": len(observation_ids), "sha": content_sha, "feature": json.dumps(feature)})
    for index, observation_id in enumerate(observation_ids, 1):
        session.execute(text("INSERT INTO epidemic.dataset_snapshot_observations(snapshot_id,observation_id,row_number) VALUES(:snapshot,:observation,:row)"), {"snapshot": snapshot_id, "observation": observation_id, "row": index})
    normalization_key = sha({"promotion": str(row["promotion_id"]), "artifact": row["artifact_sha256"]})
    session.execute(text("INSERT INTO epidemic.normalization_runs(id,retrieval_run_id,source_fetch_id,normalization_key,transform_version,country_mapping_version,quality_ruleset_version,snapshot_schema_version,status,result_code,attempt_number,max_attempts,started_at,finished_at,dataset_snapshot_id,total_source_rows,selected_source_rows,normalized_observation_count) VALUES(:id,:retrieval,:fetch,:key,'approved-declarative-mapping-v1','approved-country-binding-v1','approved-source-quality-v1',1,'succeeded','IMPORTED',1,3,:now,:now,:snapshot,:count,:count,:count)"), {"id": normalization_id, "retrieval": retrieval_id, "fetch": fetch_id, "key": normalization_key, "now": now, "snapshot": snapshot_id, "count": len(observation_ids)})
    first, last = min(x[0] for x in rows), max(x[0] for x in rows)
    session.execute(text("INSERT INTO epidemic.retrieval_window_resolutions(id,query_id,retrieval_run_id,time_window_kind,duration_iso8601,resolved_start,resolved_end,source_declared_max_date,source_data_through,cases_new_data_through,retrieved_at,cadence,observed_period_count,expected_period_count,missing_period_count,coverage_ratio,reporting_lag_days,freshness_status,cases_freshness_status,deaths_freshness_status,freshness_policy_version,source_key,source_version,adapter_version,contract_version,catalog_sha256,probe_evidence_sha256) VALUES(:id,:query,:run,:kind,'P1Y',:start,:end,:last,:last,:last,:now,'week',:count,:count,0,1.0,:lag,'current','current','not_available','approved-source-freshness-v1',:source,'approved-artifact','approved-declarative-mapping-v1','approved-source-profile-v1',:catalog,:probe)"), {"id": uuid.uuid4(), "query": query_id, "run": retrieval_id, "kind": metadata["time_window_kind"], "start": first, "end": last + dt.timedelta(days=6), "last": last + dt.timedelta(days=6), "now": now, "count": len(rows), "lag": max((now.date() - (last + dt.timedelta(days=6))).days, 0), "source": source_key, "catalog": row["mapping_sha256"], "probe": row["assessment_sha256"]})
    session.execute(text("UPDATE epidemic.data_sources SET enabled=true,updated_at=now() WHERE id=:id"), {"id": source_id})
    return snapshot_id


def process_one(session: Session, quarantine: Path) -> bool:
    promotion_id = session.execute(text("SELECT id FROM epidemic_discovery.source_promotion_runs WHERE state='PROMOTION_QUEUED' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1")).scalar_one_or_none()
    if promotion_id is None:
        return _queue_approved(session)
    row = _candidate(session, promotion_id)
    payload, rows = _verify(row, quarantine)
    session.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET state='PROMOTING',state_version=state_version+1,updated_at=now() WHERE id=:id"), {"id": promotion_id})
    _event(session, promotion_id, "PROMOTION_QUEUED", "PROMOTING", "REVALIDATION_PASSED", {"artifact_sha256": row["artifact_sha256"]})
    snapshot_id = _promote(session, row, payload, rows)
    session.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET state='IMPORTED',state_version=state_version+1,formal_snapshot_id=:snapshot,updated_at=now() WHERE id=:id"), {"id": promotion_id, "snapshot": snapshot_id})
    _event(session, promotion_id, "PROMOTING", "IMPORTED", "ATOMIC_IMPORT_SUCCEEDED", {"snapshot_id": str(snapshot_id)})
    return True


def main() -> None:
    engine = create_engine(os.environ["APP_DATABASE_URL"], hide_parameters=True)
    quarantine = Path(os.environ["DISCOVERY_QUARANTINE_ROOT"])
    stopped = False

    def halt(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, halt)
    while not stopped:
        try:
            with Session(engine) as session, session.begin():
                worked = process_one(session, quarantine)
        except Exception as exc:
            code = str(exc)[:100] or type(exc).__name__
            with Session(engine) as failure_session, failure_session.begin():
                failed_id = failure_session.execute(
                    text("SELECT id FROM epidemic_discovery.source_promotion_runs WHERE state IN ('PROMOTION_QUEUED','PROMOTING') ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED")
                ).scalar_one_or_none()
                if failed_id is not None:
                    old = failure_session.execute(text("SELECT state FROM epidemic_discovery.source_promotion_runs WHERE id=:id"), {"id": failed_id}).scalar_one()
                    failure_session.execute(text("UPDATE epidemic_discovery.source_promotion_runs SET state='PROMOTION_FAILED',state_version=state_version+1,updated_at=now() WHERE id=:id"), {"id": failed_id})
                    _event(failure_session, failed_id, old, "PROMOTION_FAILED", "ATOMIC_IMPORT_ROLLED_BACK", {"error_code": code})
            time.sleep(2)
            continue
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
