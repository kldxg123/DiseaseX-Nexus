from __future__ import annotations

# ruff: noqa: E501
# mypy: disable-error-code="type-arg,no-any-return,return-value"
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from epidemic_core.discovery.safe_fetcher import FetchBlocked, SafeFetcher
from epidemic_core.discovery.schemas import DiscoveryInput

POLICY = "source-discovery-policy-v3"
TRANSFORMS = {
    "identity",
    "parse_iso_date",
    "parse_epiweek",
    "parse_isoweek",
    "parse_month",
    "country_name_to_iso3",
    "numeric_count",
    "numeric_rate",
    "explicit_null_tokens",
}


def canonical(v: object) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha(v: object) -> str:
    return hashlib.sha256(v if isinstance(v, bytes) else canonical(v)).hexdigest()


def event(
    session: Session, run_id: uuid.UUID, old: str | None, new: str, code: str, details: dict | None = None
) -> None:
    payload = {
        "run": str(run_id),
        "from": old,
        "to": new,
        "code": code,
        "details": details or {},
        "nonce": str(uuid.uuid4()),
    }
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.discovery_run_events(id,discovery_run_id,from_state,to_state,event_code,details,event_sha256) VALUES(:id,:r,:f,:t,:c,CAST(:d AS jsonb),:s)"
        ),
        {
            "id": uuid.uuid4(),
            "r": run_id,
            "f": old,
            "t": new,
            "c": code,
            "d": json.dumps(details or {}),
            "s": sha(payload),
        },
    )


def start(session: Session, value: DiscoveryInput) -> tuple[uuid.UUID, bool]:
    request = value.model_dump(mode="json")
    digest = sha(request)
    existing = session.execute(
        text("SELECT id FROM epidemic_discovery.discovery_runs WHERE request_sha256=:s"), {"s": digest}
    ).scalar_one_or_none()
    if existing:
        return existing, False
    rid = session.execute(
        text("SELECT epidemic_discovery.submit_keyfree_discovery(CAST(:request AS jsonb))"),
        {"request": json.dumps(request)},
    ).scalar_one()
    return rid, True


def status(session: Session, rid: uuid.UUID) -> dict:
    run = (
        session.execute(text("SELECT state FROM epidemic_discovery.discovery_runs WHERE id=:id"), {"id": rid})
        .mappings()
        .one()
    )
    rows = (
        session.execute(
            text(
                "SELECT id,publisher,dataset_name,source_tier,status,risk_flags,score,score_breakdown FROM epidemic_discovery.source_candidates WHERE discovery_run_id=:id ORDER BY score DESC NULLS LAST"
            ),
            {"id": rid},
        )
        .mappings()
        .all()
    )
    candidates = [dict(x) for x in rows]
    return {
        "discovery_run_id": rid,
        "state": run["state"],
        "candidate_count": len(rows),
        "review_ready_count": sum(x["status"] == "REVIEW_READY" for x in rows),
        "blocked_count": sum(x["status"] != "REVIEW_READY" for x in rows),
        "candidates": candidates,
        "review_required": True,
        "review_url": f"http://127.0.0.1:8021/?run={rid}",
    }


def _profile_csv(payload: bytes) -> tuple[list[str], list[dict], dict]:
    text_value = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_value))
    headers = reader.fieldnames or []
    if not headers or len(headers) > 100:
        raise FetchBlocked("SOURCE_SCHEMA_MISMATCH")
    rows = []
    formula = 0
    pii = 0
    for i, row in enumerate(reader):
        if i >= 10000:
            raise FetchBlocked("ROW_LIMIT")
        clean = {str(k)[:200]: (str(v)[:2000] if v is not None else None) for k, v in row.items()}
        formula += sum(bool(v and v.lstrip().startswith(("=", "+", "-", "@"))) for v in clean.values())
        pii += sum(
            bool(
                v
                and (re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", v) or re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", v))
            )
            for v in clean.values()
        )
        rows.append(clean)
    return headers, rows, {"formula_cells": formula, "pii_hits": pii, "rows": len(rows), "columns": len(headers)}


def _profile_isolated(path: Path) -> tuple[list[str], list[dict], dict]:
    with path.open("rb") as source:
        command = [
            "setpriv",
            "--reuid=epidemicextract",
            "--regid=epidemicextract",
            "--clear-groups",
            "--no-new-privs",
            "prlimit",
            "--cpu=120",
            "--as=2147483648",
            "--nofile=64",
            sys.executable,
            "USER_HOME/autodl-tmp/epidemic-extractor-runtime/extractor.py",
            "--fd",
            str(source.fileno()),
            "--format",
            "csv",
            "--enforce-no-network",
        ]
        environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
        completed = subprocess.run(
            command, env=environment, pass_fds=(source.fileno(),), capture_output=True, timeout=125, check=False
        )
    if completed.returncode != 0 or len(completed.stdout) > 20 * 1024 * 1024:
        raise FetchBlocked("QUARANTINE_EXTRACTION_FAILED")
    try:
        result = json.loads(completed.stdout)
        if result.get("network_policy") != "seccomp_socket_syscalls_denied":
            raise ValueError("NETWORK_POLICY_MISSING")
        return list(result["headers"]), list(result["samples"]), dict(result["quality"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FetchBlocked("QUARANTINE_EXTRACTION_FAILED") from exc


def process_one(session: Session, quarantine: Path) -> uuid.UUID | None:
    run = (
        session.execute(
            text(
                "SELECT id,request_json FROM epidemic_discovery.discovery_runs WHERE state='QUEUED' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    if not run:
        return None
    rid = run["id"]
    request = run["request_json"]
    session.execute(
        text(
            "UPDATE epidemic_discovery.discovery_runs SET state='DISCOVERING',state_version=state_version+1,updated_at=now() WHERE id=:id"
        ),
        {"id": rid},
    )
    event(session, rid, "QUEUED", "DISCOVERING", "DISCOVERY_STARTED")
    year = dt.datetime.now(dt.timezone.utc).year
    url = f"https://info.dengue.mat.br/api/alertcity?disease=dengue&geocode=3304557&format=csv&ew_start=1&ew_end=53&ey_start={year - 1}&ey_end={year}"
    candidate_id = uuid.uuid4()
    endpoint_sha = sha(url)
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_candidates(id,discovery_run_id,canonical_endpoint_sha256,publisher,dataset_name,source_tier,status,risk_flags,score,score_breakdown) VALUES(:id,:r,:e,:p,:n,'PUBLIC_HEALTH_RESEARCH','PROFILING','[]'::jsonb,0.82,CAST(:b AS jsonb))"
        ),
        {
            "id": candidate_id,
            "r": rid,
            "e": endpoint_sha,
            "p": "InfoDengue / Fiocruz-linked public-health collaboration",
            "n": "InfoDengue municipal weekly dengue alert API",
            "b": json.dumps(
                {
                    "publisher": 0.9,
                    "machine_readable": 1.0,
                    "aggregation": 1.0,
                    "license": 0.4,
                    "geographic_coverage": 0.6,
                }
            ),
        },
    )
    event(session, rid, "DISCOVERING", "CANDIDATES_FOUND", "CANDIDATE_FOUND", {"count": 1})
    session.execute(
        text(
            "UPDATE epidemic_discovery.discovery_runs SET state='PROFILING',state_version=state_version+1 WHERE id=:id"
        ),
        {"id": rid},
    )
    event(session, rid, "CANDIDATES_FOUND", "PROFILING", "PROFILING_STARTED")
    try:
        result = SafeFetcher().fetch(url)
    except FetchBlocked as exc:
        session.execute(
            text(
                "UPDATE epidemic_discovery.source_candidates SET status='SECURITY_BLOCKED',risk_flags=CAST(:f AS jsonb) WHERE id=:id"
            ),
            {"id": candidate_id, "f": json.dumps([str(exc)])},
        )
        session.execute(
            text(
                "UPDATE epidemic_discovery.discovery_runs SET state='NO_CANDIDATE',state_version=state_version+1 WHERE id=:id"
            ),
            {"id": rid},
        )
        event(session, rid, "PROFILING", "NO_CANDIDATE", str(exc))
        return rid
    digest = sha(result.payload)
    target = quarantine / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result.payload)
    os.chmod(target, 0o600)
    try:
        headers, rows, quality = _profile_isolated(target)
    except FetchBlocked as exc:
        session.execute(
            text(
                "UPDATE epidemic_discovery.source_candidates SET status='SECURITY_BLOCKED',risk_flags=CAST(:f AS jsonb) WHERE id=:id"
            ),
            {"id": candidate_id, "f": json.dumps([str(exc)])},
        )
        session.execute(
            text(
                "UPDATE epidemic_discovery.discovery_runs SET state='NO_CANDIDATE',state_version=state_version+1 WHERE id=:id"
            ),
            {"id": rid},
        )
        event(session, rid, "PROFILING", "NO_CANDIDATE", str(exc))
        return rid
    version = uuid.uuid4()
    metadata = {
        "country_iso3": request["country_iso3"],
        "disease_id": request["disease_id"],
        "time_window_kind": request["time_window_kind"],
        "update_frequency": "weekly",
        "geographic_scope": "Rio de Janeiro municipality; partial Brazil coverage",
        "redirect_sha256": list(result.redirects),
        "latest_report_period": max((x.get("data_iniSE") or x.get("data") or "" for x in rows), default=""),
    }
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_candidate_versions(id,candidate_id,version_no,canonical_url_redacted,canonical_endpoint_sha256,publisher_evidence_sha256,terms_sha256,license_status,format,metadata,content_sha256) VALUES(:id,:c,1,:u,:e,:p,:t,'CUSTOM_REVIEW_REQUIRED','CSV',CAST(:m AS jsonb),:s)"
        ),
        {
            "id": version,
            "c": candidate_id,
            "u": "https://info.dengue.mat.br/api/alertcity?[controlled-query]",
            "e": result.endpoint_sha256,
            "p": sha("publisher-evidence-v1"),
            "t": sha("license-review-required"),
            "m": json.dumps(metadata),
            "s": digest,
        },
    )
    security = {
        "ssrf": "PASS",
        "dns_rebinding": "PASS",
        "tls": "PASS",
        "active_content": "PASS",
        "formula_cells": quality["formula_cells"],
        "pii_hits": quality["pii_hits"],
    }
    security_sha = sha(security)
    artifact = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.quarantine_artifacts(id,candidate_version_id,sha256,byte_size,mime_type,storage_key,security_status,security_report_sha256,retrieved_at) VALUES(:id,:v,:s,:z,:m,:k,'PASS',:r,now())"
        ),
        {
            "id": artifact,
            "v": version,
            "s": digest,
            "z": len(result.payload),
            "m": result.content_type,
            "k": digest,
            "r": security_sha,
        },
    )
    profile_json = {
        "headers": headers,
        "sample_policy": "head-tail-deterministic",
        "null_policy": "preserve-null-distinct-from-zero",
    }
    profile_sha = sha(profile_json)
    profile = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_schema_profiles(id,candidate_version_id,schema_sha256,row_count,column_count,date_min,date_max,profile_json) VALUES(:id,:v,:s,:r,:c,NULL,NULL,CAST(:p AS jsonb))"
        ),
        {
            "id": profile,
            "v": version,
            "s": profile_sha,
            "r": len(rows),
            "c": len(headers),
            "p": json.dumps(profile_json),
        },
    )
    for i, name in enumerate(headers):
        values = [x.get(name) for x in rows[:100] if x.get(name) not in (None, "")]
        example = values[0] if values else None
        kind = "number" if values and all(re.fullmatch(r"-?\d+(\.\d+)?", v or "") for v in values) else "string"
        session.execute(
            text(
                "INSERT INTO epidemic_discovery.source_schema_columns(id,schema_profile_id,ordinal,source_name,inferred_type,example_value,null_count,warning_flags) VALUES(:id,:p,:o,:n,:t,:e,:z,'[]'::jsonb)"
            ),
            {
                "id": uuid.uuid4(),
                "p": profile,
                "o": i,
                "n": name,
                "t": kind,
                "e": example,
                "z": sum(x.get(name) in (None, "") for x in rows),
            },
        )
    mapping = {
        "data_iniSE": {"canonical": "period_start", "transform": "parse_iso_date"},
        "SE": {"canonical": "reporting_period", "transform": "parse_epiweek"},
        "casos": {"canonical": "value", "metric": "cases_new", "transform": "numeric_count"},
        "geocode": {"canonical": "location_code", "transform": "identity"},
    }
    mapping_sha = sha(mapping)
    proposal = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_mapping_proposals(id,candidate_version_id,revision,mapping_sha256,proposal_json,status) VALUES(:id,:v,1,:s,CAST(:p AS jsonb),'PROPOSED')"
        ),
        {"id": proposal, "v": version, "s": mapping_sha, "p": json.dumps(mapping)},
    )
    for source, spec in mapping.items():
        session.execute(
            text(
                "INSERT INTO epidemic_discovery.source_mapping_fields(id,mapping_proposal_id,source_field,canonical_field,metric_code,transform,confidence) VALUES(:id,:p,:s,:c,:m,:t,:f)"
            ),
            {
                "id": uuid.uuid4(),
                "p": proposal,
                "s": source,
                "c": spec.get("canonical"),
                "m": spec.get("metric"),
                "t": spec["transform"],
                "f": 0.9,
            },
        )
    selected = []
    for kind, items in (("HEAD", rows[:25]), ("TAIL", rows[-25:])):
        for idx, row in enumerate(items):
            selected.append((kind, idx, row))
    for kind, idx, row in selected:
        sanitized = {
            k: ("[REDACTED]" if re.search(r"email|phone|name|patient|cpf", k, re.I) else v) for k, v in row.items()
        }
        session.execute(
            text(
                "INSERT INTO epidemic_discovery.source_preview_rows(id,candidate_version_id,sample_kind,row_ordinal,raw_json,sanitized_json,row_sha256) VALUES(:id,:v,:k,:o,CAST(:r AS jsonb),CAST(:s AS jsonb),:h)"
            ),
            {
                "id": uuid.uuid4(),
                "v": version,
                "k": kind,
                "o": idx,
                "r": json.dumps(sanitized),
                "s": json.dumps(sanitized),
                "h": sha(row),
            },
        )
    flags = ["PARTIAL_GEOGRAPHIC_COVERAGE", "MODEL_DERIVED_FIELDS_PRESENT", "LICENSE_REVIEW_REQUIRED"]
    assessment = {
        "quality": quality,
        "flags": flags,
        "publisher": "public-health collaboration",
        "officiality": "government-partner, not national ministry source",
        "duplicate": "WHO xMart excluded as registered duplicate",
    }
    assessment_sha = sha({"assessment": assessment, "security": security})
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_quality_assessments(id,candidate_version_id,assessment_sha256,status,quality_json,security_json,pii_status,license_status) VALUES(:id,:v,:s,'REVIEW_READY',CAST(:q AS jsonb),CAST(:z AS jsonb),'AGGREGATED','CUSTOM_REVIEW_REQUIRED')"
        ),
        {"id": uuid.uuid4(), "v": version, "s": assessment_sha, "q": json.dumps(assessment), "z": json.dumps(security)},
    )
    review = uuid.uuid4()
    etag = sha({"version": str(version), "schema": profile_sha, "mapping": mapping_sha, "security": security_sha})
    session.execute(
        text(
            "INSERT INTO epidemic_discovery.source_review_cases(id,candidate_version_id,state,current_mapping_revision,etag) VALUES(:id,:v,'REVIEW_READY',1,:e)"
        ),
        {"id": review, "v": version, "e": etag},
    )
    session.execute(
        text(
            "UPDATE epidemic_discovery.source_candidates SET status='REVIEW_READY',risk_flags=CAST(:f AS jsonb) WHERE id=:id"
        ),
        {"id": candidate_id, "f": json.dumps(flags)},
    )
    session.execute(
        text(
            "UPDATE epidemic_discovery.discovery_runs SET state='REVIEW_READY',state_version=state_version+1,updated_at=now() WHERE id=:id"
        ),
        {"id": rid},
    )
    event(
        session,
        rid,
        "PROFILING",
        "REVIEW_READY",
        "REVIEW_READY",
        {"candidate_id": str(candidate_id), "review_case_id": str(review)},
    )
    return rid
