from __future__ import annotations

import concurrent.futures
import datetime as dt
import decimal
import hashlib
import os
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import (
    Country,
    DataSource,
    Disease,
    MetricDefinition,
    NormalizedObservation,
    QueryRequest,
    RawArtifact,
    RetrievalRun,
    SourceFetch,
)
from epidemic_core.db.repositories import (
    ArtifactRepository,
    DatasetRepository,
    ObservationRepository,
    QualityRepository,
    QueryRepository,
)
from epidemic_core.domain.observation import NormalizedObservationCreate, QualityAssessmentCreate
from epidemic_core.domain.provenance import RawArtifactCreate
from epidemic_core.domain.query import EpidemicQueryInput, NormalizedEpidemicQuery
from epidemic_core.query_service import QueryService

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def urls() -> tuple[str, str]:
    return os.environ["MIGRATION_DATABASE_URL"], os.environ["APP_DATABASE_URL"]


@pytest.fixture(scope="module")
def engines(urls):
    migration = create_engine(urls[0], hide_parameters=True)
    app = create_engine(urls[1], pool_size=5, max_overflow=5, pool_pre_ping=True, hide_parameters=True)
    yield migration, app
    app.dispose()
    migration.dispose()


def test_reference_counts_and_chinese_resolution(engines) -> None:
    _, app = engines
    with Session(app) as s:
        assert s.scalar(select(func.count()).select_from(Country)) == 249
        assert s.scalar(select(func.count()).select_from(Disease)) == 5
        assert s.scalar(select(func.count()).select_from(MetricDefinition)) == 30
        n = QueryService().normalize(
            s,
            EpidemicQueryInput(
                country="中华人民共和国", disease="登革热", start_date="2025-01-01", end_date="2025-01-02"
            ),
        )
        assert n.country_alpha2 == "CN" and n.disease_slug == "dengue"
        ebola = QueryService().normalize(
            s,
            EpidemicQueryInput(
                country="CD",
                disease="EVD",
                start_date="2026-05-01",
                end_date="2026-07-31",
            ),
        )
        assert ebola.country_alpha2 == "CD" and ebola.disease_slug == "ebola-disease"


def test_query_idempotence_and_concurrency(engines) -> None:
    _, app = engines
    with Session(app) as s:
        disease = s.scalar(select(Disease).where(Disease.slug == "dengue"))
        assert disease
        query = NormalizedEpidemicQuery(
            country_alpha2="CN", disease_slug="dengue", start_date=dt.date(2025, 1, 1), end_date=dt.date(2025, 1, 2)
        )
        did = disease.id

    def create() -> str:
        with Session(app) as s, s.begin():
            return str(QueryRepository().create_or_get(s, "CN", "dengue", did, query).id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))
    assert len(set(ids)) == 1
    with Session(app) as s:
        assert (
            s.scalar(
                select(func.count()).select_from(QueryRequest).where(QueryRequest.fingerprint == query.fingerprint())
            )
            == 1
        )


def test_artifact_idempotence_and_concurrency(engines) -> None:
    _, app = engines
    value = RawArtifactCreate(media_type="text/plain", encoding="utf-8", payload=b"same fixture")

    def create() -> str:
        with Session(app) as s, s.begin():
            return str(
                ArtifactRepository().create_or_get(s, value, storage_mode="inline_text", inline_text="same fixture").id
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))
    assert len(set(ids)) == 1
    with Session(app) as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(RawArtifact)
                .where(RawArtifact.sha256 == hashlib.sha256(value.payload).hexdigest())
            )
            == 1
        )


def _fixture_ids(engines):
    migration, app = engines
    with Session(migration) as s, s.begin():
        source = DataSource(
            id=uuid.uuid4(),
            source_key="local-fixture",
            display_name="Local fixture",
            source_type="fixture",
            authority="test",
            base_url=None,
            adapter_name="fixture",
            enabled=True,
        )
        s.add(source)
        source_id = source.id
    with Session(app) as s, s.begin():
        q = s.scalar(select(QueryRequest))
        assert q
        run = RetrievalRun(
            id=uuid.uuid4(),
            run_key="fixture-run",
            query_request_id=q.id,
            source_id=source_id,
            adapter_version="1",
            status="succeeded",
            attempt_number=1,
            started_at=dt.datetime.now(dt.timezone.utc),
            finished_at=dt.datetime.now(dt.timezone.utc),
        )
        s.add(run)
        s.flush()
        art = s.scalar(select(RawArtifact))
        assert art
        fetch = SourceFetch(
            id=uuid.uuid4(),
            retrieval_run_id=run.id,
            source_id=source_id,
            canonical_url="fixture://local",
            requested_at=dt.datetime.now(dt.timezone.utc),
            fetched_at=dt.datetime.now(dt.timezone.utc),
            artifact_id=art.id,
            attempt_number=1,
        )
        s.add(fetch)
        s.flush()
        disease = s.scalar(select(Disease).where(Disease.slug == "dengue"))
        assert disease
        return fetch.id, disease.id, q.id


def test_observation_revision_metric_constraints_and_view(engines) -> None:
    _, app = engines
    fetch, disease, qid = _fixture_ids(engines)
    series = "1" * 64

    def obs(record: str, revision: int, value: str, sup=None, metric="cases_new"):
        return NormalizedObservationCreate(
            record_fingerprint=record,
            series_fingerprint=series,
            revision=revision,
            supersedes_observation_id=sup,
            source_fetch_id=fetch,
            country_alpha2="CN",
            disease_id=disease,
            metric_code=metric,
            value_numeric=decimal.Decimal(value),
            period_start=dt.date(2025, 1, 1),
            period_end=dt.date(2025, 1, 1),
            granularity="day",
            as_of=dt.datetime.now(dt.timezone.utc),
            dimensions={},
            normalization_version="1",
        )

    with Session(app) as s, s.begin():
        old = ObservationRepository().create_or_get(s, obs("2" * 64, 1, "-1"))
        old2 = ObservationRepository().create_or_get(s, obs("2" * 64, 1, "-1"))
        assert old.id == old2.id
        old_id = old.id
    with Session(app) as s, s.begin():
        new = ObservationRepository().create_or_get(s, obs("3" * 64, 2, "2", old_id))
        QualityRepository().create_or_get(
            s,
            QualityAssessmentCreate(
                observation_id=old_id, ruleset_version="1", status="accepted", score=decimal.Decimal("1"), flags=[]
            ),
        )
        QualityRepository().create_or_get(
            s,
            QualityAssessmentCreate(
                observation_id=new.id, ruleset_version="1", status="accepted", score=decimal.Decimal("0.9"), flags=[]
            ),
        )
        rejected = ObservationRepository().create_or_get(
            s,
            obs("6" * 64, 1, "4").model_copy(update={"series_fingerprint": "7" * 64}),
        )
        QualityRepository().create_or_get(
            s,
            QualityAssessmentCreate(
                observation_id=rejected.id,
                ruleset_version="1",
                status="rejected",
                score=decimal.Decimal("0"),
                flags=["fixture"],
            ),
        )
        rejected_id = rejected.id
        new_id = new.id
    with Session(app) as s:
        rows = s.execute(
            text("SELECT observation_id,revision FROM epidemic.training_observations_v1 WHERE series_fingerprint=:x"),
            {"x": series},
        ).all()
        assert rows == [(new_id, 2)]
        assert (
            s.execute(
                text("SELECT count(*) FROM epidemic.training_observations_v1 WHERE observation_id=:x"),
                {"x": rejected_id},
            ).scalar_one()
            == 0
        )
    with Session(app) as s, s.begin():
        repo = DatasetRepository()
        cutoff = dt.datetime(2025, 12, 31, tzinfo=dt.timezone.utc)
        first = repo.snapshot(s, qid, cutoff, {"version": 1})
        second = repo.snapshot(s, qid, cutoff, {"version": 1})
        assert first.content_sha256 == second.content_sha256
        assert first.row_count == second.row_count == 1
    with Session(app) as s, s.begin():
        s.add(
            NormalizedObservation(
                id=uuid.uuid4(),
                record_fingerprint="4" * 64,
                series_fingerprint="5" * 64,
                revision=1,
                source_fetch_id=fetch,
                country_alpha2="CN",
                disease_id=disease,
                metric_code="cases_cumulative",
                value_numeric=-1,
                period_start=dt.date(2025, 1, 1),
                period_end=dt.date(2025, 1, 1),
                granularity="day",
                as_of=dt.datetime.now(dt.timezone.utc),
                dimensions={},
                dimensions_sha256=hashlib.sha256(b"{}").hexdigest(),
                normalization_version="1",
            )
        )
    invalid_sql = (
        "INSERT INTO epidemic.normalized_observations("
        "id,record_fingerprint,series_fingerprint,revision,source_fetch_id,"
        "country_alpha2,disease_id,metric_code,value_numeric,period_start,period_end,"
        "granularity,as_of,dimensions,dimensions_sha256,normalization_version) VALUES ("
        "gen_random_uuid(),'bad','bad',1,:f,'CN',:d,'cases_new',1,"
        "'2025-02-01','2025-01-01','day',CURRENT_TIMESTAMP,'{}','bad','1')"
    )
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.execute(text(invalid_sql), {"f": fetch, "d": disease})
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.execute(text("UPDATE epidemic.retrieval_runs SET status='invalid' WHERE run_key='fixture-run'"))
    with Session(app) as s, pytest.raises(IntegrityError), s.begin():
        s.add(
            NormalizedObservation(
                id=uuid.uuid4(),
                record_fingerprint="8" * 64,
                series_fingerprint="9" * 64,
                revision=1,
                source_fetch_id=fetch,
                country_alpha2="CN",
                disease_id=disease,
                metric_code="reproduction_number",
                value_numeric=21,
                period_start=dt.date(2025, 1, 1),
                period_end=dt.date(2025, 1, 1),
                granularity="day",
                as_of=dt.datetime.now(dt.timezone.utc),
                dimensions={},
                dimensions_sha256=hashlib.sha256(b"{}").hexdigest(),
                normalization_version="1",
            )
        )
    assert app.pool.checkedout() == 0


def test_rejected_not_in_view_and_sql_payload_safe(engines) -> None:
    migration, app = engines
    with Session(migration) as s, s.begin():
        source = DataSource(
            id=uuid.uuid4(),
            source_key="malicious'; DROP TABLE epidemic.countries; --",
            display_name="payload",
            source_type="fixture",
            authority="test",
            adapter_name="fixture",
            enabled=False,
        )
        s.add(source)
    with Session(app) as s:
        assert s.scalar(select(func.count()).select_from(Country)) == 249
