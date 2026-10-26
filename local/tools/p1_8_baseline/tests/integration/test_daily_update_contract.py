from __future__ import annotations

import datetime as dt
import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from epidemic_core.daily.service import DailyUpdateService
from epidemic_core.daily.worker import DailyUpdateCoordinator
from epidemic_core.db.models.tables import (
    DatasetSnapshot,
    DataSource,
    Disease,
    NormalizationRun,
    QueryRequest,
    RawArtifact,
    RetrievalRun,
    SourceFetch,
)
from epidemic_core.db.session import session_factory

pytestmark = pytest.mark.integration
PROJECT = Path("USER_HOME/autodl-tmp/epidemic-openclaw")
NOW = dt.datetime(2026, 7, 14, 3, 0, tzinfo=dt.timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _seed_isolated_display_targets(session: Session) -> None:
    targets = (
        ("FR", "covid-19", "who_covid19_global_weekly", "who_covid19_weekly", "who-covid19-v2"),
        ("BR", "dengue", "who_dengue_global_validated", "who_dengue_xmart", "who-dengue-v1"),
    )
    for country, disease_slug, source_key, adapter, contract in targets:
        disease = session.scalar(select(Disease).where(Disease.slug == disease_slug))
        assert disease is not None
        source_id, query_id, retrieval_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        artifact_id, fetch_id, snapshot_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        session.add(
            DataSource(
                id=source_id,
                source_key=source_key,
                display_name=f"P1-8 isolated fixture {source_key}",
                source_type="official_fixture",
                authority="test-only",
                adapter_name=adapter,
                enabled=True,
            )
        )
        session.add(
            QueryRequest(
                id=query_id,
                fingerprint=_sha(f"query:{source_key}"),
                raw_country=country,
                raw_disease=disease_slug,
                country_alpha2=country,
                disease_id=disease.id,
                start_date=None,
                end_date=None,
                time_window_kind="latest_rolling_year",
                duration_iso8601="P1Y",
                source_preference="official",
                schema_version=2,
            )
        )
        session.flush()
        session.add(
            RetrievalRun(
                id=retrieval_id,
                run_key=f"p1-8-isolated-{source_key}",
                query_request_id=query_id,
                source_id=source_id,
                adapter_version="fixture-v1",
                status="succeeded",
                attempt_number=1,
                started_at=NOW,
                finished_at=NOW,
            )
        )
        payload = f"fixture:{source_key}"
        session.add(
            RawArtifact(
                id=artifact_id,
                sha256=_sha(payload),
                byte_size=len(payload.encode()),
                media_type="text/plain",
                encoding="utf-8",
                storage_mode="inline_text",
                inline_text=payload,
            )
        )
        session.flush()
        session.add(
            SourceFetch(
                id=fetch_id,
                retrieval_run_id=retrieval_id,
                source_id=source_id,
                canonical_url=f"https://fixture.invalid/{source_key}",
                requested_at=NOW,
                fetched_at=NOW,
                http_status=200,
                artifact_id=artifact_id,
                attempt_number=1,
                response_content_type="text/plain",
                response_byte_size=len(payload.encode()),
            )
        )
        session.add(
            DatasetSnapshot(
                id=snapshot_id,
                query_request_id=query_id,
                snapshot_schema_version=1,
                cutoff_at=NOW,
                row_count=0,
                content_sha256=_sha(f"snapshot:{source_key}"),
                feature_spec={"normalization_contract_version": contract, "fixture": True},
            )
        )
        session.flush()
        session.add(
            NormalizationRun(
                id=uuid.uuid4(),
                retrieval_run_id=retrieval_id,
                source_fetch_id=fetch_id,
                normalization_key=_sha(f"normalization:{source_key}"),
                transform_version="fixture-v1",
                country_mapping_version="fixture-v1",
                quality_ruleset_version="fixture-v1",
                snapshot_schema_version=1,
                status="succeeded",
                result_code="FIXTURE_READY",
                attempt_number=1,
                max_attempts=3,
                started_at=NOW,
                finished_at=NOW,
                dataset_snapshot_id=snapshot_id,
            )
        )
        session.flush()


@pytest.fixture(scope="module")
def engines():
    migration = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    app = create_engine(os.environ["APP_DATABASE_URL"], hide_parameters=True, pool_size=12, max_overflow=4)
    with Session(migration) as session, session.begin():
        eligible = (
            session.execute(
                text(
                    "SELECT DISTINCT ds.source_key FROM epidemic.dataset_snapshots snap "
                    "JOIN epidemic.query_requests q ON q.id=snap.query_request_id "
                    "JOIN epidemic.normalization_runs nr ON nr.dataset_snapshot_id=snap.id AND nr.status='succeeded' "
                    "JOIN epidemic.retrieval_runs rr ON rr.id=nr.retrieval_run_id "
                    "JOIN epidemic.data_sources ds ON ds.id=rr.source_id "
                    "WHERE ds.enabled AND q.time_window_kind='latest_rolling_year' "
                    "AND ds.source_key IN ('who_covid19_global_weekly','who_dengue_global_validated')"
                )
            )
            .scalars()
            .all()
        )
        if not eligible:
            _seed_isolated_display_targets(session)
        seeded = DailyUpdateService.seed_schedules(session, PROJECT / "config/daily_update.json", NOW)
        assert len(seeded.schedule_ids) == 2
    yield migration, app
    app.dispose()
    migration.dispose()


@pytest.fixture(autouse=True)
def isolate_runs(engines) -> None:
    migration, _ = engines
    with migration.begin() as connection:
        connection.execute(
            text("TRUNCATE epidemic.daily_update_run_events,epidemic.daily_update_runs RESTART IDENTITY")
        )
        connection.execute(
            text(
                "UPDATE epidemic.daily_update_schedules SET next_due_at=:due,last_checked_at=NULL,"
                "last_success_at=NULL,last_source_change_at=NULL,consecutive_failures=0,enabled=true"
            ),
            {"due": NOW + dt.timedelta(days=1)},
        )


def _reconcile(app, trigger: str, now: dt.datetime):
    with Session(app) as session, session.begin():
        return DailyUpdateService.reconcile_due(session, trigger, now)


def test_ten_concurrent_cron_reconciles_create_one_run_per_schedule(engines) -> None:
    migration, app = engines
    with migration.begin() as connection:
        connection.execute(
            text("UPDATE epidemic.daily_update_schedules SET next_due_at=:due"),
            {"due": NOW - dt.timedelta(days=4)},
        )
    with ThreadPoolExecutor(max_workers=10) as pool:
        batches = list(pool.map(lambda _: _reconcile(app, "openclaw-cron", NOW), range(10)))
    rows = [row for batch in batches for row in batch]
    assert len({row.run_id for row in rows}) == 2
    assert all(row.missed_intervals == 5 for row in rows)
    with migration.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM epidemic.daily_update_runs")).scalar_one() == 2
        assert (
            connection.execute(
                text("SELECT count(*) FROM epidemic.daily_update_run_events WHERE event_code='DAILY_UPDATE_QUEUED'")
            ).scalar_one()
            == 2
        )


def test_startup_catchup_coalesces_and_advances_due_time(engines) -> None:
    migration, app = engines
    with migration.begin() as connection:
        connection.execute(
            text("UPDATE epidemic.daily_update_schedules SET next_due_at=:due"),
            {"due": NOW - dt.timedelta(days=2)},
        )
    rows = _reconcile(app, "startup", NOW)
    assert len(rows) == 2 and all(row.missed_intervals == 3 for row in rows)
    with migration.connect() as connection:
        values = connection.execute(
            text(
                "SELECT trigger_type,next_due_at FROM epidemic.daily_update_runs "
                "JOIN epidemic.daily_update_schedules USING(schedule_id)"
            )
        ).all()
    assert all(row.trigger_type == "STARTUP_CATCH_UP" for row in values)
    assert all(row.next_due_at > NOW for row in values)


def test_ten_manual_requests_share_one_logical_day_run(engines) -> None:
    migration, app = engines
    with migration.connect() as connection:
        schedule_id = connection.execute(
            text("SELECT schedule_id FROM epidemic.daily_update_schedules ORDER BY schedule_id LIMIT 1")
        ).scalar_one()

    def start() -> uuid.UUID:
        with Session(app) as session, session.begin():
            return DailyUpdateService.start_manual(session, schedule_id, NOW)

    with ThreadPoolExecutor(max_workers=10) as pool:
        run_ids = list(pool.map(lambda _: start(), range(10)))
    assert len(set(run_ids)) == 1
    with migration.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM epidemic.daily_update_run_events "
                    "WHERE run_id=:run AND event_code='DAILY_UPDATE_QUEUED'"
                ),
                {"run": run_ids[0]},
            ).scalar_one()
            == 1
        )


def test_expired_daily_claim_is_recovered(engines) -> None:
    migration, app = engines
    with migration.connect() as connection:
        schedule_id = connection.execute(
            text("SELECT schedule_id FROM epidemic.daily_update_schedules ORDER BY schedule_id LIMIT 1")
        ).scalar_one()
    with Session(app) as session, session.begin():
        run_id = DailyUpdateService.start_manual(session, schedule_id, NOW)
    with migration.begin() as connection:
        connection.execute(
            text(
                "UPDATE epidemic.daily_update_runs SET state='FETCHING',claim_owner='dead-worker',"
                "claim_token=gen_random_uuid(),claimed_at=:now,lease_expires_at=:expired,heartbeat_at=:expired "
                "WHERE run_id=:run"
            ),
            {"run": run_id, "now": NOW, "expired": NOW - dt.timedelta(seconds=1)},
        )
    coordinator = DailyUpdateCoordinator(
        session_factory(app), PROJECT / "reference/data_sources.v2.json", clock=lambda: NOW
    )
    assert coordinator.recover() == 1
    with migration.connect() as connection:
        row = connection.execute(
            text("SELECT claim_token,claim_owner FROM epidemic.daily_update_runs WHERE run_id=:run"),
            {"run": run_id},
        ).one()
        assert row == (None, None)


def test_disabled_source_finishes_without_network(engines) -> None:
    migration, app = engines
    with migration.connect() as connection:
        schedule_id, source_id = connection.execute(
            text("SELECT schedule_id,data_source_id FROM epidemic.daily_update_schedules ORDER BY schedule_id LIMIT 1")
        ).one()
    with Session(app) as session, session.begin():
        run_id = DailyUpdateService.start_manual(session, schedule_id, NOW)
    with migration.begin() as connection:
        connection.execute(text("UPDATE epidemic.data_sources SET enabled=false WHERE id=:id"), {"id": source_id})
    try:
        coordinator = DailyUpdateCoordinator(
            session_factory(app), PROJECT / "reference/data_sources.v2.json", clock=lambda: NOW
        )
        assert coordinator.run_once()
        with migration.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT state FROM epidemic.daily_update_runs WHERE run_id=:run"), {"run": run_id}
                ).scalar_one()
                == "CANCELLED_SOURCE_DISABLED"
            )
    finally:
        with migration.begin() as connection:
            connection.execute(text("UPDATE epidemic.data_sources SET enabled=true WHERE id=:id"), {"id": source_id})


def test_event_log_is_append_only(engines) -> None:
    migration, app = engines
    with migration.connect() as connection:
        schedule_id = connection.execute(
            text("SELECT schedule_id FROM epidemic.daily_update_schedules ORDER BY schedule_id LIMIT 1")
        ).scalar_one()
    with Session(app) as session, session.begin():
        DailyUpdateService.start_manual(session, schedule_id, NOW)
    with pytest.raises(DBAPIError), migration.begin() as connection:
        connection.execute(text("UPDATE epidemic.daily_update_run_events SET event_code='tampered'"))


def test_app_role_rejects_ten_out_of_scope_mutations(engines) -> None:
    migration, app = engines
    with migration.connect() as connection:
        schedule_id = connection.execute(
            text("SELECT schedule_id FROM epidemic.daily_update_schedules ORDER BY schedule_id LIMIT 1")
        ).scalar_one()
    with Session(app) as session, session.begin():
        run_id = DailyUpdateService.start_manual(session, schedule_id, NOW)
    statements = [
        "DELETE FROM epidemic.daily_update_schedules",
        "UPDATE epidemic.daily_update_schedules SET enabled=false",
        "UPDATE epidemic.daily_update_schedules SET schedule_name='forbidden'",
        "UPDATE epidemic.daily_update_schedules SET query_request_id=gen_random_uuid()",
        "INSERT INTO epidemic.daily_update_runs(run_id) VALUES(gen_random_uuid())",
        "DELETE FROM epidemic.daily_update_runs",
        "UPDATE epidemic.daily_update_runs SET logical_date=CURRENT_DATE-1",
        "UPDATE epidemic.daily_update_runs SET schedule_id=gen_random_uuid()",
        "UPDATE epidemic.daily_update_run_events SET event_code='forbidden'",
        "DELETE FROM epidemic.daily_update_run_events",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError), app.begin() as connection:
            connection.execute(text(statement))
    with migration.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM epidemic.daily_update_runs WHERE run_id=:run"), {"run": run_id}
            ).scalar_one()
            == 1
        )
