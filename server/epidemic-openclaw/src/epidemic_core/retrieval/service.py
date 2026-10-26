from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import (
    Country,
    Disease,
    QueryRequest,
    RetrievalRun,
    RetrievalRunEvent,
    RetrievalWindowResolution,
)
from epidemic_core.retrieval.registry import load_registry, select_source
from epidemic_core.retrieval.windows import (
    EXPLICIT_RANGE,
    FRESHNESS_POLICY_VERSION,
    WINDOW_CONTRACT_VERSION,
    WindowResolution,
    WindowResolutionError,
    probe_source,
)

POLICY_VERSION = "source-policy-v1"
PLAN_VERSION = "retrieval-plan-v3"
MAX_RUN_ATTEMPTS = 3


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnqueueResult:
    run: RetrievalRun
    created: bool
    retried: bool
    check_evidence: WindowResolution | None


class RetrievalService:
    def __init__(self, registry_path: Path):
        self.registry_path = registry_path

    def enqueue(self, session: Session, query_id: uuid.UUID) -> EnqueueResult:
        query = session.get(QueryRequest, query_id)
        if query is None:
            raise RetrievalError("QUERY_NOT_FOUND")
        disease = session.get(Disease, query.disease_id)
        if disease is None:
            raise RetrievalError("QUERY_NOT_FOUND")
        _, catalog_sha, specs = load_registry(self.registry_path)
        source, spec = select_source(session, specs, disease.slug)
        resolution = None
        if query.time_window_kind != EXPLICIT_RANGE:
            country = session.get(Country, query.country_alpha2)
            if country is None:
                raise RetrievalError("QUERY_NOT_FOUND")
            try:
                resolution = probe_source(spec, country.alpha2, country.alpha3, query.time_window_kind)
            except WindowResolutionError as exc:
                raise RetrievalError(str(exc)) from exc
        evidence = (
            f"{resolution.resolved_start}|{resolution.resolved_end}|{resolution.source_data_through}|{resolution.probe_evidence_sha256}"
            if resolution
            else f"{query.start_date}|{query.end_date}|explicit"
        )
        material = "|".join(
            (
                query.fingerprint,
                spec.source_key,
                spec.adapter_version,
                POLICY_VERSION,
                PLAN_VERSION,
                catalog_sha,
                evidence,
            )
        )
        run_key = hashlib.sha256(material.encode()).hexdigest()
        row = session.execute(
            select(RetrievalRun).where(RetrievalRun.run_key == run_key).with_for_update()
        ).scalar_one_or_none()
        if row is not None:
            if row.status == "failed":
                if row.attempt_number >= MAX_RUN_ATTEMPTS:
                    raise RetrievalError("RETRY_EXHAUSTED")
                old = row.status
                row.status = "pending"
                row.attempt_number += 1
                row.started_at = None
                row.finished_at = None
                row.error_code = None
                row.error_message = None
                row.claim_token = None
                row.claimed_at = None
                row.lease_expires_at = None
                row.next_attempt_at = None
                session.add(
                    RetrievalRunEvent(
                        retrieval_run_id=row.id,
                        from_status=old,
                        to_status="pending",
                        event_code="RETRIEVAL_REQUEUED",
                        details={"attempt_number": row.attempt_number},
                    )
                )
                return EnqueueResult(row, False, True, resolution)
            return EnqueueResult(row, False, False, resolution)
        run_id = uuid.uuid4()
        stmt = (
            insert(RetrievalRun)
            .values(
                id=run_id,
                run_key=run_key,
                query_request_id=query.id,
                source_id=source.id,
                adapter_version=spec.adapter_version,
                status="pending",
                attempt_number=1,
            )
            .on_conflict_do_nothing(index_elements=[RetrievalRun.run_key])
            .returning(RetrievalRun.id)
        )
        created_id = session.execute(stmt).scalar_one_or_none()
        if created_id is None:
            row = session.execute(select(RetrievalRun).where(RetrievalRun.run_key == run_key)).scalar_one()
            return EnqueueResult(row, False, False, resolution)
        row = session.get(RetrievalRun, created_id)
        assert row is not None
        if resolution is not None:
            session.add(
                RetrievalWindowResolution(
                    id=uuid.uuid4(),
                    query_id=query.id,
                    retrieval_run_id=row.id,
                    time_window_kind=resolution.time_window_kind,
                    duration_iso8601=resolution.duration_iso8601,
                    resolved_start=resolution.resolved_start,
                    resolved_end=resolution.resolved_end,
                    source_declared_max_date=resolution.source_declared_max_date,
                    source_data_through=resolution.source_data_through,
                    cases_new_data_through=resolution.cases_new_data_through,
                    deaths_new_data_through=resolution.deaths_new_data_through,
                    cases_cumulative_data_through=resolution.cases_cumulative_data_through,
                    deaths_cumulative_data_through=resolution.deaths_cumulative_data_through,
                    source_http_last_modified=resolution.source_http_last_modified,
                    retrieved_at=resolution.retrieved_at,
                    cadence=resolution.cadence,
                    observed_period_count=resolution.observed_period_count,
                    expected_period_count=resolution.expected_period_count,
                    missing_period_count=resolution.missing_period_count,
                    coverage_ratio=resolution.coverage_ratio,
                    reporting_lag_days=resolution.reporting_lag_days,
                    freshness_status=resolution.freshness_status,
                    cases_freshness_status=resolution.cases_freshness_status,
                    deaths_freshness_status=resolution.deaths_freshness_status,
                    freshness_policy_version=FRESHNESS_POLICY_VERSION,
                    source_key=spec.source_key,
                    source_version="2",
                    adapter_version=spec.adapter_version,
                    contract_version=WINDOW_CONTRACT_VERSION,
                    catalog_sha256=catalog_sha,
                    probe_evidence_sha256=resolution.probe_evidence_sha256,
                )
            )
        session.add(
            RetrievalRunEvent(
                retrieval_run_id=row.id,
                from_status=None,
                to_status="pending",
                event_code="RETRIEVAL_ENQUEUED",
                details={"source_key": spec.source_key, "policy_version": POLICY_VERSION, "plan_version": PLAN_VERSION},
            )
        )
        return EnqueueResult(row, True, False, resolution)

    @staticmethod
    def claim(session: Session, now: dt.datetime, lease_seconds: int = 180) -> tuple[RetrievalRun, uuid.UUID] | None:
        row = session.execute(
            select(RetrievalRun)
            .where(
                RetrievalRun.status == "pending",
                (RetrievalRun.next_attempt_at.is_(None)) | (RetrievalRun.next_attempt_at <= now),
            )
            .order_by(RetrievalRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        token = uuid.uuid4()
        row.status = "running"
        row.claim_token = token
        row.claimed_at = now
        row.lease_expires_at = now + dt.timedelta(seconds=lease_seconds)
        row.started_at = now
        row.finished_at = None
        session.add(
            RetrievalRunEvent(
                retrieval_run_id=row.id,
                from_status="pending",
                to_status="running",
                event_code="WORKER_CLAIMED",
                details={"attempt_number": row.attempt_number},
            )
        )
        return row, token

    @staticmethod
    def reconcile_expired(session: Session, now: dt.datetime) -> int:
        rows = (
            session.execute(
                select(RetrievalRun)
                .where(RetrievalRun.status == "running", RetrievalRun.lease_expires_at < now)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.finished_at = now
            row.error_code = "WORKER_INTERRUPTED"
            row.error_message = None
            row.claim_token = None
            session.add(
                RetrievalRunEvent(
                    retrieval_run_id=row.id,
                    from_status="running",
                    to_status="failed",
                    event_code="WORKER_INTERRUPTED",
                    details={"attempt_number": row.attempt_number},
                )
            )
        return len(rows)

    @staticmethod
    def finish(
        session: Session,
        run_id: uuid.UUID,
        claim_token: uuid.UUID,
        status: str,
        error_code: str | None,
        now: dt.datetime,
    ) -> bool:
        values = {
            "status": status,
            "finished_at": now,
            "error_code": error_code,
            "error_message": None,
            "claim_token": None,
            "lease_expires_at": None,
        }
        result = session.execute(
            update(RetrievalRun)
            .where(RetrievalRun.id == run_id, RetrievalRun.status == "running", RetrievalRun.claim_token == claim_token)
            .values(**values)
        )
        if getattr(result, "rowcount", 0) != 1:
            return False
        session.add(
            RetrievalRunEvent(
                retrieval_run_id=run_id,
                from_status="running",
                to_status=status,
                event_code="RETRIEVAL_SUCCEEDED" if status == "succeeded" else (error_code or "RETRIEVAL_FAILED"),
                details={},
            )
        )
        return True
