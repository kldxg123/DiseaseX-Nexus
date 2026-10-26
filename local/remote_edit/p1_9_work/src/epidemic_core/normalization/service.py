from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import (
    DatasetSnapshot,
    DatasetSnapshotObservation,
    DataSource,
    Disease,
    MetricDefinition,
    NormalizationRun,
    NormalizationRunEvent,
    NormalizedObservation,
    QualityAssessment,
    QueryRequest,
    RawArtifact,
    RetrievalRun,
    RetrievalWindowResolution,
    SourceFetch,
)
from epidemic_core.normalization.contract import Contract, Versions, load_contract
from epidemic_core.normalization.parser import ParsedArtifact, SourceRow, parse_artifact, verify_artifact

MAX_ATTEMPTS = 3
DIMENSIONS = {
    "date_basis": "reported",
    "period_label": "week_end",
    "period_semantics": "reporting_bucket_not_event_occurrence",
    "source_frequency": "weekly",
}


class NormalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedObservation:
    metric_code: str
    value: decimal.Decimal
    raw_value: str
    reported: dt.date
    flags: tuple[str, ...]


@dataclass(frozen=True)
class NormalizationPlan:
    run_id: uuid.UUID
    retrieval_run_id: uuid.UUID
    source_fetch_id: uuid.UUID
    query_id: uuid.UUID
    query_start: dt.date
    query_end: dt.date
    country_alpha2: str
    disease_id: uuid.UUID
    disease_slug: str
    source_key: str
    artifact_sha256: str
    artifact_byte_size: int
    last_modified: str | None
    retrieved_at: dt.datetime
    contract: Contract
    parsed: ParsedArtifact
    observations: tuple[PlannedObservation, ...]


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sha(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class NormalizationService:
    @staticmethod
    def enqueue(
        session: Session, retrieval: RetrievalRun, fetch: SourceFetch, artifact_sha256: str, versions: Versions
    ) -> NormalizationRun:
        material = {
            "retrieval_run_key": retrieval.run_key,
            "source_fetch_id": str(fetch.id),
            "artifact_sha256": artifact_sha256,
            "transform": versions.transform,
            "mapping": versions.country_mapping,
            "quality": versions.quality_ruleset,
            "snapshot": versions.snapshot_schema,
        }
        key = sha(material)
        run_id = uuid.uuid4()
        stmt = (
            insert(NormalizationRun)
            .values(
                id=run_id,
                retrieval_run_id=retrieval.id,
                source_fetch_id=fetch.id,
                normalization_key=key,
                transform_version=versions.transform,
                country_mapping_version=versions.country_mapping,
                quality_ruleset_version=versions.quality_ruleset,
                snapshot_schema_version=versions.snapshot_schema,
                status="pending",
                attempt_number=1,
                max_attempts=MAX_ATTEMPTS,
            )
            .on_conflict_do_nothing(index_elements=[NormalizationRun.normalization_key])
            .returning(NormalizationRun.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        row = (
            session.get(NormalizationRun, created)
            if created
            else session.execute(select(NormalizationRun).where(NormalizationRun.normalization_key == key)).scalar_one()
        )
        assert row is not None
        if created:
            session.add(
                NormalizationRunEvent(
                    normalization_run_id=row.id,
                    from_status=None,
                    to_status="pending",
                    event_code="NORMALIZATION_ENQUEUED",
                    details={"artifact_sha256": artifact_sha256},
                )
            )
        return row

    @staticmethod
    def claim(session: Session, now: dt.datetime, lease_seconds: int = 300) -> tuple[uuid.UUID, uuid.UUID] | None:
        row = session.execute(
            select(NormalizationRun)
            .where(
                NormalizationRun.status == "pending",
                (NormalizationRun.next_attempt_at.is_(None)) | (NormalizationRun.next_attempt_at <= now),
            )
            .order_by(NormalizationRun.created_at)
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
            NormalizationRunEvent(
                normalization_run_id=row.id,
                from_status="pending",
                to_status="running",
                event_code="NORMALIZATION_CLAIMED",
                details={"attempt_number": row.attempt_number},
            )
        )
        return row.id, token

    @staticmethod
    def recover(session: Session, now: dt.datetime) -> int:
        rows = (
            session.execute(
                select(NormalizationRun)
                .where(NormalizationRun.status == "running", NormalizationRun.lease_expires_at < now)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.finished_at = now
            row.error_code = "WORKER_INTERRUPTED"
            row.claim_token = None
            row.lease_expires_at = None
            session.add(
                NormalizationRunEvent(
                    normalization_run_id=row.id,
                    from_status="running",
                    to_status="failed",
                    event_code="WORKER_INTERRUPTED",
                    details={"attempt_number": row.attempt_number},
                )
            )
        return len(rows)

    @staticmethod
    def retry_failed(session: Session, retrieval_run_id: uuid.UUID) -> bool:
        row = session.execute(
            select(NormalizationRun).where(NormalizationRun.retrieval_run_id == retrieval_run_id).with_for_update()
        ).scalar_one_or_none()
        if row is None or row.status != "failed":
            return False
        if row.attempt_number >= row.max_attempts:
            raise NormalizationError("NORMALIZATION_RETRY_EXHAUSTED")
        row.status = "pending"
        row.attempt_number += 1
        row.next_attempt_at = None
        row.claim_token = None
        row.claimed_at = None
        row.lease_expires_at = None
        row.started_at = None
        row.finished_at = None
        row.error_code = None
        row.result_code = None
        session.add(
            NormalizationRunEvent(
                normalization_run_id=row.id,
                from_status="failed",
                to_status="pending",
                event_code="NORMALIZATION_REQUEUED",
                details={"attempt_number": row.attempt_number},
            )
        )
        return True


def _quality_flags(row: SourceRow, previous: SourceRow | None, global_latest: dt.date) -> dict[int, set[str]]:
    flags: dict[int, set[str]] = {i: set() for i in range(4)}
    common: set[str] = set()
    if row.exact_duplicate:
        common.add("EXACT_DUPLICATE_SOURCE_ROW")
    if row.reported == global_latest:
        common.add("LATEST_PERIOD_POSSIBLY_INCOMPLETE")
    if previous is not None and (row.reported - previous.reported).days > 7:
        common.add("REPORTING_GAP_BEFORE")
    for values in flags.values():
        values.update(common)
    new_cases = row.values[0]
    new_deaths = row.values[2]
    if new_cases is not None and new_cases < 0:
        flags[0].add("RETROSPECTIVE_NEGATIVE_REVISION")
    if new_deaths is not None and new_deaths < 0:
        flags[2].add("RETROSPECTIVE_NEGATIVE_REVISION")
    for i in (1, 3):
        current_cumulative = row.values[i]
        if current_cumulative is not None and current_cumulative < 0:
            flags[i].add("NEGATIVE_CUMULATIVE_VALUE")
        previous_cumulative = previous.values[i] if previous is not None else None
        if current_cumulative is not None and previous_cumulative is not None:
            if current_cumulative < previous_cumulative:
                flags[i].add("CUMULATIVE_REVISION")
            new_i = i - 1
            reported_new = row.values[new_i]
            if reported_new is not None and current_cumulative - previous_cumulative != reported_new:
                flags[i].add("REPORTED_NEW_DIFFERS_FROM_CUMULATIVE_DELTA")
                flags[new_i].add("REPORTED_NEW_DIFFERS_FROM_CUMULATIVE_DELTA")
    return flags


def build_plan(session: Session, run_id: uuid.UUID, reference_root: Path, raw_root: Path) -> NormalizationPlan:
    run = session.get(NormalizationRun, run_id)
    if run is None:
        raise NormalizationError("NORMALIZATION_NOT_FOUND")
    retrieval = session.get(RetrievalRun, run.retrieval_run_id)
    fetch = session.get(SourceFetch, run.source_fetch_id)
    if retrieval is None or fetch is None or fetch.artifact_id is None:
        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
    artifact = session.get(RawArtifact, fetch.artifact_id)
    query = session.get(QueryRequest, retrieval.query_request_id)
    source = session.get(DataSource, retrieval.source_id)
    if artifact is None or query is None or source is None or fetch.fetched_at is None:
        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
    disease = session.get(Disease, query.disease_id)
    if disease is None:
        raise NormalizationError("QUERY_NOT_FOUND")
    contract, mapping = load_contract(session, reference_root)
    path = Path(artifact.storage_uri or "").resolve()
    root = raw_root.resolve()
    if root not in path.parents or path.name != artifact.sha256 + ".csv":
        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
    verify_artifact(path, artifact.sha256, artifact.byte_size)
    resolution = session.scalar(
        select(RetrievalWindowResolution).where(RetrievalWindowResolution.retrieval_run_id == retrieval.id)
    )
    query_start = resolution.resolved_start if resolution else query.start_date
    query_end = resolution.resolved_end if resolution else query.end_date
    if query_start is None or query_end is None:
        raise NormalizationError("WINDOW_RESOLUTION_MISSING")
    parsed = parse_artifact(path, contract, mapping, query.country_alpha2, query_start, query_end)
    ordered = list(parsed.country_rows)
    positions = {x.reported: i for i, x in enumerate(ordered)}
    fields = contract.expected_header[4:]
    observations: list[PlannedObservation] = []
    for row in parsed.selected_rows:
        pos = positions[row.reported]
        previous = ordered[pos - 1] if pos else None
        flags = _quality_flags(row, previous, parsed.global_latest_date)
        for index, field in enumerate(fields):
            value = row.values[index]
            if value is not None:
                observations.append(
                    PlannedObservation(
                        contract.metric_mapping[field],
                        value,
                        row.raw_values[index],
                        row.reported,
                        tuple(sorted(flags[index])),
                    )
                )
    return NormalizationPlan(
        run.id,
        retrieval.id,
        fetch.id,
        query.id,
        query_start,
        query_end,
        query.country_alpha2,
        disease.id,
        disease.slug,
        source.source_key,
        artifact.sha256,
        artifact.byte_size,
        fetch.last_modified,
        fetch.fetched_at,
        contract,
        parsed,
        tuple(observations),
    )


def finalize_plan(
    session: Session, run_id: uuid.UUID, token: uuid.UUID, plan: NormalizationPlan, now: dt.datetime
) -> uuid.UUID | None:
    run = session.execute(select(NormalizationRun).where(NormalizationRun.id == run_id).with_for_update()).scalar_one()
    if run.status != "running" or run.claim_token != token:
        raise NormalizationError("STALE_CLAIM")
    metric_rows = {x.metric_code: x for x in session.execute(select(MetricDefinition)).scalars()}
    observation_rows: list[NormalizedObservation] = []
    warnings = 0
    for item in plan.observations:
        effective_flags = set(item.flags)
        metric = metric_rows.get(item.metric_code)
        if metric is None:
            raise NormalizationError("METRIC_CONTRACT_ERROR")
        period_end = item.reported
        period_start = period_end - dt.timedelta(days=6)
        series_material = {
            "source_key": plan.source_key,
            "country": plan.country_alpha2,
            "disease": plan.disease_slug,
            "metric": item.metric_code,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "granularity": "week",
            "dimensions": DIMENSIONS,
        }
        series_fingerprint = sha(series_material)
        record_fingerprint = sha({"series": series_material, "value": str(item.value)})
        lock_key = (
            int(series_fingerprint[:16], 16) - (1 << 64)
            if int(series_fingerprint[:16], 16) >= 1 << 63
            else int(series_fingerprint[:16], 16)
        )
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        observation = session.execute(
            select(NormalizedObservation).where(NormalizedObservation.record_fingerprint == record_fingerprint)
        ).scalar_one_or_none()
        if observation is None:
            previous = session.execute(
                select(NormalizedObservation)
                .where(NormalizedObservation.series_fingerprint == series_fingerprint)
                .order_by(NormalizedObservation.revision.desc())
                .limit(1)
            ).scalar_one_or_none()
            if metric.cumulative and previous is not None and item.value < previous.value_numeric:
                effective_flags.add("CUMULATIVE_REVISION")
            observation = NormalizedObservation(
                id=uuid.uuid4(),
                record_fingerprint=record_fingerprint,
                series_fingerprint=series_fingerprint,
                revision=1 if previous is None else previous.revision + 1,
                supersedes_observation_id=previous.id if previous else None,
                source_fetch_id=plan.source_fetch_id,
                country_alpha2=plan.country_alpha2,
                disease_id=plan.disease_id,
                metric_code=item.metric_code,
                value_numeric=item.value,
                raw_value=item.raw_value,
                period_start=period_start,
                period_end=period_end,
                granularity="week",
                reporting_date=period_end,
                as_of=plan.retrieved_at,
                dimensions=dict(DIMENSIONS),
                dimensions_sha256=sha(DIMENSIONS),
                normalization_version=plan.contract.versions.transform,
            )
            session.add(observation)
            session.flush()
        quality = session.execute(
            select(QualityAssessment).where(
                QualityAssessment.observation_id == observation.id,
                QualityAssessment.ruleset_version == plan.contract.versions.quality_ruleset,
            )
        ).scalar_one_or_none()
        if quality is None:
            score = decimal.Decimal("1.0") if not effective_flags else decimal.Decimal("0.5")
            status = "accepted" if not effective_flags else "quarantined"
            quality = QualityAssessment(
                id=uuid.uuid4(),
                observation_id=observation.id,
                ruleset_version=plan.contract.versions.quality_ruleset,
                status=status,
                score=score,
                flags=sorted(effective_flags),
                assessed_at=now,
            )
            session.add(quality)
        warnings += len(effective_flags)
        observation_rows.append(observation)
    snapshot_id: uuid.UUID | None = None
    if observation_rows:
        window = session.scalar(
            select(RetrievalWindowResolution).where(RetrievalWindowResolution.retrieval_run_id == plan.retrieval_run_id)
        )
        ordered = sorted(
            observation_rows,
            key=lambda x: (x.period_end, x.metric_code, x.series_fingerprint, x.revision, x.record_fingerprint),
        )
        feature_spec = {
            "source_key": plan.source_key,
            "artifact_sha256": plan.artifact_sha256,
            "source_fetch_id": str(plan.source_fetch_id),
            "who_last_modified": plan.last_modified,
            "retrieved_at": plan.retrieved_at.isoformat(),
            "cutoff_at": plan.retrieved_at.isoformat(),
            "country_mapping_version": plan.contract.versions.country_mapping,
            "transform_version": plan.contract.versions.transform,
            "quality_ruleset_version": plan.contract.versions.quality_ruleset,
            "normalization_contract_version": plan.contract.contract_version,
            "normalization_contract_sha256": plan.contract.contract_sha256,
            "metric_catalog_version": plan.contract.metrics_version,
            "metric_catalog_sha256": plan.contract.metrics_sha256,
            "snapshot_schema_version": plan.contract.versions.snapshot_schema,
            "country": plan.country_alpha2,
            "disease": plan.disease_slug,
            "requested_start": plan.query_start.isoformat(),
            "requested_end": plan.query_end.isoformat(),
            "time_window_kind": window.time_window_kind if window else "explicit_range",
            "resolved_start": window.resolved_start.isoformat() if window else plan.query_start.isoformat(),
            "resolved_end": window.resolved_end.isoformat() if window else plan.query_end.isoformat(),
            "source_data_through": window.source_data_through.isoformat() if window else None,
            "latest_window_contract_version": window.contract_version if window else None,
            "metrics": sorted({x.metric_code for x in plan.observations}),
            "date_semantics": DIMENSIONS,
            "missing_policy": "sparse/no-fill",
            "negative_revision_policy": "preserve-and-flag",
            "latest_period_policy": "preserve-and-flag-possibly-incomplete",
            "attribution": plan.contract.attribution,
            "terms_url": plan.contract.terms_url,
            "endorsement_disclaimer": plan.contract.disclaimer,
        }
        hash_rows = [
            {
                "country": x.country_alpha2,
                "disease": plan.disease_slug,
                "metric": x.metric_code,
                "value": str(x.value_numeric),
                "period_start": x.period_start.isoformat(),
                "period_end": x.period_end.isoformat(),
                "granularity": x.granularity,
                "revision": x.revision,
                "series_fingerprint": x.series_fingerprint,
                "record_fingerprint": x.record_fingerprint,
            }
            for x in ordered
        ]
        deterministic_feature_spec = {
            k: v for k, v in feature_spec.items() if k not in {"source_fetch_id", "retrieved_at", "cutoff_at"}
        }
        content_sha = sha({"feature_spec": deterministic_feature_spec, "rows": hash_rows})
        existing = session.execute(
            select(DatasetSnapshot).where(
                DatasetSnapshot.query_request_id == plan.query_id,
                DatasetSnapshot.snapshot_schema_version == plan.contract.versions.snapshot_schema,
                DatasetSnapshot.content_sha256 == content_sha,
            )
        ).scalar_one_or_none()
        if existing is None:
            snapshot = DatasetSnapshot(
                id=uuid.uuid4(),
                query_request_id=plan.query_id,
                snapshot_schema_version=plan.contract.versions.snapshot_schema,
                cutoff_at=plan.retrieved_at,
                row_count=len(ordered),
                content_sha256=content_sha,
                feature_spec=feature_spec,
            )
            session.add(snapshot)
            session.flush()
            for number, observation in enumerate(ordered, 1):
                session.add(
                    DatasetSnapshotObservation(
                        snapshot_id=snapshot.id, observation_id=observation.id, row_number=number
                    )
                )
            snapshot_id = snapshot.id
        else:
            snapshot_id = existing.id
    result_code = "NO_OBSERVATIONS" if not observation_rows else "NORMALIZED"
    result = session.execute(
        update(NormalizationRun)
        .where(
            NormalizationRun.id == run_id, NormalizationRun.status == "running", NormalizationRun.claim_token == token
        )
        .values(
            status="succeeded",
            result_code=result_code,
            finished_at=now,
            claim_token=None,
            lease_expires_at=None,
            error_code=None,
            dataset_snapshot_id=snapshot_id,
            total_source_rows=plan.parsed.total_rows,
            selected_source_rows=len(plan.parsed.selected_rows),
            normalized_observation_count=len(observation_rows),
            missing_value_count=plan.parsed.missing_values,
            warning_count=warnings,
            unmapped_source_code_count=len(plan.parsed.unmapped_codes),
            exact_duplicate_count=plan.parsed.exact_duplicates,
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        raise NormalizationError("STALE_CLAIM")
    session.add(
        NormalizationRunEvent(
            normalization_run_id=run_id,
            from_status="running",
            to_status="succeeded",
            event_code=result_code,
            details={
                "selected_source_rows": len(plan.parsed.selected_rows),
                "normalized_observation_count": len(observation_rows),
                "missing_value_count": plan.parsed.missing_values,
                "warning_count": warnings,
                "unmapped_source_code_count": len(plan.parsed.unmapped_codes),
                "exact_duplicate_count": plan.parsed.exact_duplicates,
            },
        )
    )
    return snapshot_id


def fail_run(session: Session, run_id: uuid.UUID, token: uuid.UUID, code: str, now: dt.datetime) -> bool:
    result = session.execute(
        update(NormalizationRun)
        .where(
            NormalizationRun.id == run_id, NormalizationRun.status == "running", NormalizationRun.claim_token == token
        )
        .values(
            status="failed", result_code=None, finished_at=now, claim_token=None, lease_expires_at=None, error_code=code
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        return False
    session.add(
        NormalizationRunEvent(
            normalization_run_id=run_id, from_status="running", to_status="failed", event_code=code, details={}
        )
    )
    return True
