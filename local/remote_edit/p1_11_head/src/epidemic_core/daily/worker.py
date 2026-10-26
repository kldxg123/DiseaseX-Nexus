from __future__ import annotations

import datetime as dt
import logging
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from epidemic_core.db.models.tables import (
    DailyUpdateRun,
    DailyUpdateRunEvent,
    DailyUpdateSchedule,
    DatasetSnapshot,
    DatasetSnapshotObservation,
    DataSource,
    NormalizationRun,
    NormalizedObservation,
    RetrievalRun,
    RetrievalRunEvent,
    RetrievalWindowResolution,
)
from epidemic_core.normalization.service import NormalizationError, NormalizationService
from epidemic_core.retrieval.service import RetrievalError, RetrievalService

log = logging.getLogger("daily_update_coordinator")
ACTIVE_STATES = {"QUEUED", "CLAIMED", "FETCHING", "NORMALIZING", "SNAPSHOTTING", "FAILED_RETRYABLE"}
TERMINAL_STATES = {
    "SUCCEEDED_CHANGED",
    "SUCCEEDED_NO_CHANGE",
    "PARTIAL",
    "FAILED_TERMINAL",
    "CANCELLED_SOURCE_DISABLED",
}


class DailyUpdateCoordinator:
    def __init__(
        self,
        factory: sessionmaker[Session],
        registry_path: Path,
        *,
        worker_id: str = "daily-update-coordinator-v1",
        clock: Callable[[], dt.datetime] | None = None,
    ):
        self.factory = factory
        self.registry_path = registry_path
        self.worker_id = worker_id
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.stop_requested = False

    def recover(self) -> int:
        now = self.clock()
        with self.factory() as session, session.begin():
            rows = (
                session.execute(
                    select(DailyUpdateRun)
                    .where(
                        DailyUpdateRun.state.in_(ACTIVE_STATES),
                        DailyUpdateRun.claim_token.is_not(None),
                        DailyUpdateRun.lease_expires_at < now,
                    )
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            for row in rows:
                session.add(
                    DailyUpdateRunEvent(
                        run_id=row.run_id,
                        from_state=row.state,
                        to_state=row.state,
                        event_code="DAILY_CLAIM_EXPIRED_RECOVERED",
                        details={"worker_id": self.worker_id},
                    )
                )
                row.claim_owner = None
                row.claim_token = None
                row.claimed_at = None
                row.lease_expires_at = None
                row.heartbeat_at = None
            return len(rows)

    def _claim(self) -> tuple[uuid.UUID, uuid.UUID] | None:
        now = self.clock()
        with self.factory() as session, session.begin():
            row = session.execute(
                select(DailyUpdateRun)
                .where(
                    DailyUpdateRun.state.in_(ACTIVE_STATES),
                    or_(DailyUpdateRun.retry_not_before.is_(None), DailyUpdateRun.retry_not_before <= now),
                    or_(DailyUpdateRun.claim_token.is_(None), DailyUpdateRun.lease_expires_at < now),
                )
                .order_by(DailyUpdateRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.claim_token is not None:
                session.add(
                    DailyUpdateRunEvent(
                        run_id=row.run_id,
                        from_state=row.state,
                        to_state=row.state,
                        event_code="DAILY_CLAIM_EXPIRED_RECOVERED",
                        details={"worker_id": self.worker_id},
                    )
                )
            old = row.state
            if row.state in {"QUEUED", "FAILED_RETRYABLE"}:
                row.state = "CLAIMED"
                row.started_at = row.started_at or now
                row.retry_not_before = None
            token = uuid.uuid4()
            row.claim_owner = self.worker_id
            row.claim_token = token
            row.claimed_at = now
            row.heartbeat_at = now
            row.lease_expires_at = now + dt.timedelta(seconds=180)
            session.add(
                DailyUpdateRunEvent(
                    run_id=row.run_id,
                    from_state=old,
                    to_state=row.state,
                    event_code="DAILY_UPDATE_CLAIMED",
                    details={"worker_id": self.worker_id},
                )
            )
            return row.run_id, token

    @staticmethod
    def _assert_claim(row: DailyUpdateRun, token: uuid.UUID) -> None:
        if row.claim_token != token or row.state in TERMINAL_STATES:
            raise RuntimeError("STALE_DAILY_CLAIM")

    def _release(
        self,
        session: Session,
        row: DailyUpdateRun,
        token: uuid.UUID,
        state: str,
        code: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self._assert_claim(row, token)
        old = row.state
        row.state = state
        row.claim_owner = None
        row.claim_token = None
        row.claimed_at = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        session.add(
            DailyUpdateRunEvent(
                run_id=row.run_id,
                from_state=old,
                to_state=state,
                event_code=code,
                details=details or {},
            )
        )

    def _fail(self, run_id: uuid.UUID, token: uuid.UUID, reason: str, retryable: bool) -> None:
        now = self.clock()
        with self.factory() as session, session.begin():
            row = session.execute(
                select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
            ).scalar_one()
            if row.claim_token != token or row.state in TERMINAL_STATES:
                return
            schedule = session.get(DailyUpdateSchedule, row.schedule_id)
            can_retry = retryable and row.retry_count < row.max_retries
            state = "FAILED_RETRYABLE" if can_retry else "FAILED_TERMINAL"
            self._release(session, row, token, state, reason)
            row.final_reason_code = reason
            if can_retry:
                row.retry_count += 1
                row.retry_not_before = now + dt.timedelta(seconds=5 * (2**row.retry_count))
                row.finished_at = None
            else:
                row.retry_not_before = None
                row.finished_at = now
            if schedule is not None:
                schedule.last_checked_at = now
                schedule.consecutive_failures += 1
                schedule.updated_at = now
                schedule.state_version += 1

    def _enqueue_retrieval(self, run_id: uuid.UUID, token: uuid.UUID) -> None:
        with self.factory() as session, session.begin():
            row = session.execute(
                select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
            ).scalar_one()
            self._assert_claim(row, token)
            schedule = session.get(DailyUpdateSchedule, row.schedule_id)
            if schedule is None:
                raise RuntimeError("DAILY_SCHEDULE_MISSING")
            source = session.get(DataSource, schedule.data_source_id)
            if source is None or not source.enabled:
                self._release(session, row, token, "CANCELLED_SOURCE_DISABLED", "SOURCE_DISABLED")
                row.final_reason_code = "SOURCE_DISABLED"
                row.finished_at = self.clock()
                schedule.last_checked_at = row.finished_at
                schedule.consecutive_failures += 1
                schedule.updated_at = row.finished_at
                schedule.state_version += 1
                return
            result = RetrievalService(self.registry_path).enqueue(session, schedule.query_request_id)
            row.retrieval_run_id = result.run.id
            evidence = result.check_evidence
            details: dict[str, object] = {
                "retrieval_run_id": str(result.run.id),
                "created": result.created,
                "retried": result.retried,
                "http_status": 200 if evidence is not None else None,
            }
            if evidence is not None:
                details.update(
                    {
                        "checked_at": evidence.retrieved_at.isoformat(),
                        "source_data_through": evidence.source_data_through.isoformat(),
                        "evidence_sha256": evidence.probe_evidence_sha256,
                        "last_modified_present": evidence.source_http_last_modified is not None,
                    }
                )
            self._release(session, row, token, "FETCHING", "SOURCE_CHECK_COMPLETED", details)

    def _retry_retrieval(self, run_id: uuid.UUID, token: uuid.UUID, query_id: uuid.UUID) -> None:
        with self.factory() as session, session.begin():
            row = session.execute(
                select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
            ).scalar_one()
            self._assert_claim(row, token)
            result = RetrievalService(self.registry_path).enqueue(session, query_id)
            row.retrieval_run_id = result.run.id
            self._release(session, row, token, "FETCHING", "RETRIEVAL_RETRY_ENQUEUED")

    def _finish(self, run_id: uuid.UUID, token: uuid.UUID, normalization_id: uuid.UUID) -> None:
        now = self.clock()
        with self.factory() as session, session.begin():
            row = session.execute(
                select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
            ).scalar_one()
            self._assert_claim(row, token)
            schedule = session.get(DailyUpdateSchedule, row.schedule_id)
            normalization = session.get(NormalizationRun, normalization_id)
            retrieval = session.get(RetrievalRun, row.retrieval_run_id)
            if schedule is None or normalization is None or retrieval is None:
                raise RuntimeError("DAILY_LINKAGE_MISSING")
            snapshot = (
                session.get(DatasetSnapshot, normalization.dataset_snapshot_id)
                if normalization.dataset_snapshot_id is not None
                else None
            )
            raw_stored = bool(
                session.scalar(
                    select(func.count())
                    .select_from(RetrievalRunEvent)
                    .where(
                        RetrievalRunEvent.retrieval_run_id == retrieval.id,
                        RetrievalRunEvent.event_code == "RAW_ARTIFACT_STORED",
                        RetrievalRunEvent.event_at >= (row.started_at or row.created_at),
                    )
                )
            )
            added = int(
                session.scalar(
                    select(func.count())
                    .select_from(NormalizedObservation)
                    .where(
                        NormalizedObservation.source_fetch_id == normalization.source_fetch_id,
                        NormalizedObservation.revision == 1,
                    )
                )
                or 0
            )
            revised = int(
                session.scalar(
                    select(func.count())
                    .select_from(NormalizedObservation)
                    .where(
                        NormalizedObservation.source_fetch_id == normalization.source_fetch_id,
                        NormalizedObservation.revision > 1,
                    )
                )
                or 0
            )
            excluded = int(
                normalization.missing_value_count
                + normalization.unmapped_source_code_count
                + normalization.exact_duplicate_count
            )
            if not raw_stored:
                added = 0
                revised = 0
                excluded = 0
            snapshot_new = bool(snapshot is not None and snapshot.created_at >= (row.started_at or row.created_at))
            resolution = session.execute(
                select(RetrievalWindowResolution).where(RetrievalWindowResolution.retrieval_run_id == retrieval.id)
            ).scalar_one_or_none()
            normalized_through = None
            if snapshot is not None:
                normalized_through = session.scalar(
                    select(func.max(NormalizedObservation.period_end))
                    .join(
                        DatasetSnapshotObservation,
                        DatasetSnapshotObservation.observation_id == NormalizedObservation.id,
                    )
                    .where(DatasetSnapshotObservation.snapshot_id == snapshot.id)
                )
            state = "SUCCEEDED_CHANGED" if snapshot_new else "SUCCEEDED_NO_CHANGE"
            if snapshot_new:
                reason = "SOURCE_CHANGED_SNAPSHOT_PUBLISHED"
            elif raw_stored:
                reason = "SOURCE_CHANGED_TARGET_UNCHANGED"
            else:
                reason = "SOURCE_UNCHANGED_BY_EVIDENCE_SHA"
            self._release(
                session,
                row,
                token,
                state,
                reason,
                {
                    "source_changed": raw_stored,
                    "snapshot_created": snapshot_new,
                    "observations_added": added,
                    "observations_revised": revised,
                    "rows_excluded": excluded,
                },
            )
            row.normalization_run_id = normalization.id
            row.snapshot_id = snapshot.id if snapshot is not None else None
            row.source_changed = raw_stored
            row.observations_added = added
            row.observations_revised = revised
            row.rows_excluded = excluded
            row.final_reason_code = reason
            row.finished_at = now
            schedule.last_checked_at = now
            schedule.last_success_at = now
            if raw_stored:
                schedule.last_source_change_at = now
            if resolution is not None:
                schedule.source_data_through = resolution.source_data_through
            if normalized_through is not None:
                schedule.normalized_data_through = normalized_through
            schedule.consecutive_failures = 0
            schedule.updated_at = now
            schedule.state_version += 1

    def _advance(self, run_id: uuid.UUID, token: uuid.UUID) -> None:
        with self.factory() as session:
            row = session.get(DailyUpdateRun, run_id)
            if row is None or row.retrieval_run_id is None:
                raise RuntimeError("DAILY_LINKAGE_MISSING")
            retrieval = session.get(RetrievalRun, row.retrieval_run_id)
            schedule = session.get(DailyUpdateSchedule, row.schedule_id)
            if retrieval is None or schedule is None:
                raise RuntimeError("DAILY_LINKAGE_MISSING")
            retrieval_status = retrieval.status
            query_id = schedule.query_request_id
            normalization = (
                session.execute(
                    select(NormalizationRun)
                    .where(NormalizationRun.retrieval_run_id == retrieval.id)
                    .order_by(NormalizationRun.created_at.desc())
                )
                .scalars()
                .first()
            )
        if retrieval_status in {"pending", "running"}:
            with self.factory() as session, session.begin():
                row = session.execute(
                    select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
                ).scalar_one()
                self._release(session, row, token, "FETCHING", "RETRIEVAL_IN_PROGRESS")
            return
        if retrieval_status == "failed":
            try:
                self._retry_retrieval(run_id, token, query_id)
            except RetrievalError as exc:
                self._fail(run_id, token, str(exc), retryable=False)
            return
        if retrieval_status != "succeeded":
            self._fail(run_id, token, "RETRIEVAL_TERMINAL_STATE", retryable=False)
            return
        if normalization is None:
            with self.factory() as session, session.begin():
                row = session.execute(
                    select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
                ).scalar_one()
                self._release(session, row, token, "NORMALIZING", "NORMALIZATION_AWAITED")
            return
        if normalization.status == "failed":
            try:
                with self.factory() as session, session.begin():
                    row = session.execute(
                        select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
                    ).scalar_one()
                    self._assert_claim(row, token)
                    if not NormalizationService.retry_failed(session, retrieval.id):
                        raise NormalizationError("NORMALIZATION_RETRY_UNAVAILABLE")
                    row.normalization_run_id = normalization.id
                    self._release(session, row, token, "NORMALIZING", "NORMALIZATION_RETRY_ENQUEUED")
            except NormalizationError as exc:
                self._fail(run_id, token, str(exc), retryable=False)
            return
        if normalization.status in {"pending", "running"}:
            with self.factory() as session, session.begin():
                row = session.execute(
                    select(DailyUpdateRun).where(DailyUpdateRun.run_id == run_id).with_for_update()
                ).scalar_one()
                row.normalization_run_id = normalization.id
                self._release(session, row, token, "NORMALIZING", "NORMALIZATION_IN_PROGRESS")
            return
        if normalization.status != "succeeded":
            self._fail(run_id, token, "NORMALIZATION_TERMINAL_STATE", retryable=False)
            return
        self._finish(run_id, token, normalization.id)

    def run_once(self) -> bool:
        claimed = self._claim()
        if claimed is None:
            return False
        run_id, token = claimed
        try:
            with self.factory() as session:
                row = session.get(DailyUpdateRun, run_id)
                retrieval_id = row.retrieval_run_id if row is not None else None
            if retrieval_id is None:
                self._enqueue_retrieval(run_id, token)
            else:
                self._advance(run_id, token)
        except (RetrievalError, NormalizationError) as exc:
            self._fail(run_id, token, str(exc), retryable=True)
        except RuntimeError as exc:
            if str(exc) != "STALE_DAILY_CLAIM":
                self._fail(run_id, token, "DAILY_COORDINATOR_ERROR", retryable=False)
        except Exception:
            log.exception("unexpected daily coordinator failure", extra={"run_id": str(run_id)})
            self._fail(run_id, token, "DAILY_COORDINATOR_ERROR", retryable=False)
        return True

    def serve(self, poll_seconds: float = 2.0) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop_requested", True))
        self.recover()
        log.info("worker_version=daily-update-coordinator-v1")
        while not self.stop_requested:
            if not self.run_once():
                time.sleep(poll_seconds)
