from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import (
    CountryAlias,
    DatasetSnapshot,
    DatasetSnapshotObservation,
    DataSource,
    Disease,
    DiseaseAlias,
    NormalizedObservation,
    QualityAssessment,
    QueryRequest,
    RawArtifact,
    RetrievalRun,
)
from epidemic_core.domain.dataset import DatasetSnapshotResult
from epidemic_core.domain.observation import NormalizedObservationCreate, QualityAssessmentCreate
from epidemic_core.domain.provenance import RawArtifactCreate, RetrievalRunCreate
from epidemic_core.domain.query import NormalizedEpidemicQuery

T = TypeVar("T")


def _one(session: Session, statement: Select[tuple[T]]) -> T:
    return session.execute(statement).scalar_one()


class CountryRepository:
    def aliases(self, session: Session) -> list[tuple[str, str]]:
        return list(session.execute(select(CountryAlias.alias, CountryAlias.country_alpha2)).tuples())


class DiseaseRepository:
    def aliases(self, session: Session) -> list[tuple[str, str]]:
        return list(
            session.execute(
                select(DiseaseAlias.alias, Disease.slug).join(Disease, Disease.id == DiseaseAlias.disease_id)
            ).tuples()
        )

    def id_for_slug(self, session: Session, slug: str) -> uuid.UUID:
        return _one(session, select(Disease.id).where(Disease.slug == slug))


class QueryRepository:
    def get_by_id(self, session: Session, query_id: uuid.UUID) -> QueryRequest | None:
        return session.execute(select(QueryRequest).where(QueryRequest.id == query_id)).scalar_one_or_none()

    def get_by_fingerprint(self, session: Session, fingerprint: str) -> QueryRequest | None:
        return session.execute(select(QueryRequest).where(QueryRequest.fingerprint == fingerprint)).scalar_one_or_none()

    def create_or_get_result(
        self,
        session: Session,
        raw_country: str,
        raw_disease: str,
        disease_id: uuid.UUID,
        query: NormalizedEpidemicQuery,
    ) -> tuple[QueryRequest, bool]:
        values = {
            "id": uuid.uuid4(),
            "fingerprint": query.fingerprint(),
            "raw_country": raw_country,
            "raw_disease": raw_disease,
            "country_alpha2": query.country_alpha2,
            "disease_id": disease_id,
            "start_date": query.start_date,
            "end_date": query.end_date,
            "time_window_kind": query.time_window_kind,
            "duration_iso8601": query.duration_iso8601,
            "source_preference": query.source_preference,
            "schema_version": query.schema_version,
        }
        stmt = (
            insert(QueryRequest)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[QueryRequest.fingerprint])
            .returning(QueryRequest.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        row = (
            _one(session, select(QueryRequest).where(QueryRequest.id == created))
            if created
            else _one(session, select(QueryRequest).where(QueryRequest.fingerprint == query.fingerprint()))
        )
        return row, created is not None

    def create_or_get(
        self,
        session: Session,
        raw_country: str,
        raw_disease: str,
        disease_id: uuid.UUID,
        query: NormalizedEpidemicQuery,
    ) -> QueryRequest:
        row, _created = self.create_or_get_result(session, raw_country, raw_disease, disease_id, query)
        return row


class DataSourceRepository:
    def create_or_get(self, session: Session, **values: Any) -> DataSource:
        values.setdefault("id", uuid.uuid4())
        stmt = (
            insert(DataSource)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[DataSource.source_key])
            .returning(DataSource.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        return (
            _one(session, select(DataSource).where(DataSource.id == created))
            if created
            else _one(session, select(DataSource).where(DataSource.source_key == values["source_key"]))
        )


class RetrievalRunRepository:
    def create_or_get(self, session: Session, value: RetrievalRunCreate) -> RetrievalRun:
        data = value.model_dump()
        data["id"] = uuid.uuid4()
        stmt = (
            insert(RetrievalRun)
            .values(**data)
            .on_conflict_do_nothing(index_elements=[RetrievalRun.run_key])
            .returning(RetrievalRun.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        return (
            _one(session, select(RetrievalRun).where(RetrievalRun.id == created))
            if created
            else _one(session, select(RetrievalRun).where(RetrievalRun.run_key == value.run_key))
        )


class ArtifactRepository:
    def create_or_get(
        self,
        session: Session,
        value: RawArtifactCreate,
        *,
        storage_mode: str,
        storage_uri: str | None = None,
        inline_text: str | None = None,
        inline_json: dict[str, Any] | None = None,
    ) -> RawArtifact:
        digest = hashlib.sha256(value.payload).hexdigest()
        data = {
            "id": uuid.uuid4(),
            "sha256": digest,
            "byte_size": len(value.payload),
            "media_type": value.media_type,
            "encoding": value.encoding,
            "storage_mode": storage_mode,
            "storage_uri": storage_uri,
            "inline_text": inline_text,
            "inline_json": inline_json,
        }
        stmt = (
            insert(RawArtifact)
            .values(**data)
            .on_conflict_do_nothing(index_elements=[RawArtifact.sha256])
            .returning(RawArtifact.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        return (
            _one(session, select(RawArtifact).where(RawArtifact.id == created))
            if created
            else _one(session, select(RawArtifact).where(RawArtifact.sha256 == digest))
        )


class ObservationRepository:
    def create_or_get(self, session: Session, value: NormalizedObservationCreate) -> NormalizedObservation:
        data = value.model_dump()
        data["id"] = uuid.uuid4()
        encoded = json.dumps(data["dimensions"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        data["dimensions_sha256"] = hashlib.sha256(encoded).hexdigest()
        stmt = (
            insert(NormalizedObservation)
            .values(**data)
            .on_conflict_do_nothing(index_elements=[NormalizedObservation.record_fingerprint])
            .returning(NormalizedObservation.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        return (
            _one(session, select(NormalizedObservation).where(NormalizedObservation.id == created))
            if created
            else _one(
                session,
                select(NormalizedObservation).where(
                    NormalizedObservation.record_fingerprint == value.record_fingerprint
                ),
            )
        )


class QualityRepository:
    def create_or_get(self, session: Session, value: QualityAssessmentCreate) -> QualityAssessment:
        data = value.model_dump()
        data["id"] = uuid.uuid4()
        stmt = (
            insert(QualityAssessment)
            .values(**data)
            .on_conflict_do_nothing(
                index_elements=[QualityAssessment.observation_id, QualityAssessment.ruleset_version]
            )
            .returning(QualityAssessment.id)
        )
        created = session.execute(stmt).scalar_one_or_none()
        return (
            _one(session, select(QualityAssessment).where(QualityAssessment.id == created))
            if created
            else _one(
                session,
                select(QualityAssessment).where(
                    QualityAssessment.observation_id == value.observation_id,
                    QualityAssessment.ruleset_version == value.ruleset_version,
                ),
            )
        )


class DatasetRepository:
    ORDER = "country_alpha2,disease_slug,metric_code,period_start,period_end,as_of_utc,record_fingerprint"

    def rows(self, session: Session) -> Sequence[Any]:
        return (
            session.execute(text(f"SELECT * FROM epidemic.training_observations_v1 ORDER BY {self.ORDER}"))
            .mappings()
            .all()
        )

    def snapshot(
        self, session: Session, query_request_id: uuid.UUID, cutoff_at: Any, feature_spec: dict[str, Any]
    ) -> DatasetSnapshotResult:
        rows = self.rows(session)
        encoded = b"\n".join(
            json.dumps(dict(r), sort_keys=True, separators=(",", ":"), default=str).encode() for r in rows
        )
        digest = hashlib.sha256(encoded).hexdigest()
        sid = uuid.uuid4()
        created = session.execute(
            insert(DatasetSnapshot)
            .values(
                id=sid,
                query_request_id=query_request_id,
                snapshot_schema_version=1,
                cutoff_at=cutoff_at,
                row_count=len(rows),
                content_sha256=digest,
                feature_spec=feature_spec,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    DatasetSnapshot.query_request_id,
                    DatasetSnapshot.snapshot_schema_version,
                    DatasetSnapshot.content_sha256,
                ]
            )
            .returning(DatasetSnapshot.id)
        ).scalar_one_or_none()
        if created is None:
            existing = session.execute(
                select(DatasetSnapshot).where(
                    DatasetSnapshot.query_request_id == query_request_id,
                    DatasetSnapshot.snapshot_schema_version == 1,
                    DatasetSnapshot.content_sha256 == digest,
                )
            ).scalar_one()
            return DatasetSnapshotResult(
                snapshot_id=existing.id, row_count=existing.row_count, content_sha256=existing.content_sha256
            )
        for number, row in enumerate(rows, 1):
            session.add(
                DatasetSnapshotObservation(snapshot_id=sid, observation_id=row["observation_id"], row_number=number)
            )
        return DatasetSnapshotResult(snapshot_id=sid, row_count=len(rows), content_sha256=digest)
