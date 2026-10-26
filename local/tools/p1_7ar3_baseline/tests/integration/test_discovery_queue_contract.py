from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from epidemic_core.discovery.worker import Queue, process_claim

pytestmark = pytest.mark.integration


@pytest.fixture()
def engine():
    value = create_engine(os.environ["MIGRATION_DATABASE_URL"], pool_size=12, max_overflow=4)
    yield value
    value.dispose()


@pytest.fixture()
def disease_id(engine) -> uuid.UUID:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT id FROM epidemic.diseases WHERE active ORDER BY slug LIMIT 1")
        ).scalar_one()


@pytest.fixture(autouse=True)
def isolate_queue(engine) -> None:
    """Tests run only on a disposable restored database; isolate each claim scenario."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE epidemic_discovery.discovery_runs SET state='NO_CANDIDATE',"
                "terminal_reason_code='TEST_FIXTURE_ISOLATION',completed_at=now(),"
                "claim_token=NULL,claim_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL "
                "WHERE state IN ('QUEUED','DISCOVERING','CANDIDATES_FOUND','PROFILING','FAILED_RETRYABLE')"
            )
        )


def _submit(engine, disease_id: uuid.UUID, language: str) -> uuid.UUID:
    with engine.begin() as connection:
        return connection.execute(
            text(
                "SELECT epidemic_discovery.submit_structured_discovery("
                "'CHN',:disease,ARRAY['cases_new'],'latest_rolling_year',"
                "ARRAY['NATIONAL_HEALTH'],ARRAY[:language],5)"
            ),
            {"disease": disease_id, "language": language},
        ).scalar_one()


def test_commit_visibility_and_concurrent_idempotency(engine, disease_id) -> None:
    language = "queue-visibility-" + uuid.uuid4().hex
    first = engine.connect()
    transaction = first.begin()
    run_id = first.execute(
        text(
            "SELECT epidemic_discovery.submit_structured_discovery("
            "'CHN',:disease,ARRAY['cases_new'],'latest_rolling_year',"
            "ARRAY['NATIONAL_HEALTH'],ARRAY[:language],5)"
        ),
        {"disease": disease_id, "language": language},
    ).scalar_one()
    with engine.connect() as observer:
        assert (
            observer.execute(
                text("SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE id=:id"), {"id": run_id}
            ).scalar_one()
            == 0
        )
    transaction.commit()
    first.close()
    with engine.connect() as observer:
        assert (
            observer.execute(
                text("SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE id=:id"), {"id": run_id}
            ).scalar_one()
            == 1
        )

    concurrent_language = "queue-concurrent-" + uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(lambda _: _submit(engine, disease_id, concurrent_language), range(10)))
    assert len(set(ids)) == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM epidemic_discovery.discovery_run_events "
                    "WHERE discovery_run_id=:id AND event_code='DISCOVERY_QUEUED'"
                ),
                {"id": ids[0]},
            ).scalar_one()
            == 1
        )


def test_double_claim_heartbeat_stale_recovery_and_old_token_rejected(engine, disease_id) -> None:
    first_id = _submit(engine, disease_id, "claim-a-" + uuid.uuid4().hex)
    second_id = _submit(engine, disease_id, "claim-b-" + uuid.uuid4().hex)
    token_a, token_b = uuid.uuid4(), uuid.uuid4()

    def claim(worker: str, token: uuid.UUID):
        with engine.begin() as connection:
            return (
                connection.execute(
                    text("SELECT * FROM epidemic_discovery.claim_discovery_run(:worker,:token,10)"),
                    {"worker": worker, "token": token},
                )
                .mappings()
                .first()
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda args: claim(*args), (("worker-a", token_a), ("worker-b", token_b))))
    claimed = {row["run_id"] for row in rows if row}
    assert {first_id, second_id}.issubset(claimed)
    row_a = next(row for row in rows if row and row["claim_token"] == token_a)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT epidemic_discovery.heartbeat_discovery_claim(:run,:token,'worker-a',10)"),
            {"run": row_a["run_id"], "token": token_a},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE epidemic_discovery.discovery_runs SET lease_expires_at=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": row_a["run_id"]},
        )
    replacement = claim("worker-recovery", uuid.uuid4())
    assert replacement and replacement["run_id"] == row_a["run_id"]
    with engine.begin() as connection:
        assert not connection.execute(
            text(
                "SELECT epidemic_discovery.finish_discovery_claim("
                ":run,:token,'NO_CANDIDATE','OLD_TOKEN',CAST('{}' AS jsonb),CAST('{}' AS jsonb),0)"
            ),
            {"run": row_a["run_id"], "token": token_a},
        ).scalar_one()


def test_invalid_legacy_payload_and_no_provider_terminal(engine, disease_id) -> None:
    payload = {"country_iso3": "CHN"}
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    legacy_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO epidemic_discovery.discovery_runs("
                "id,state,request_json,request_sha256,policy_version,budget_json) "
                "VALUES(:id,'QUEUED',CAST(:payload AS jsonb),:sha,'test',CAST('{}' AS jsonb))"
            ),
            {"id": legacy_id, "payload": json.dumps(payload), "sha": digest},
        )
    queue = Queue(engine, "test-worker-invalid", dt.datetime.now(dt.timezone.utc))
    assert queue.claim() is None
    with engine.connect() as connection:
        invalid = connection.execute(
            text("SELECT state,terminal_reason_code FROM epidemic_discovery.discovery_runs WHERE id=:id"),
            {"id": legacy_id},
        ).one()
    assert invalid == ("INVALID_REQUEST", "MISSING_OR_INVALID_REQUEST_FIELDS")

    run_id = _submit(engine, disease_id, "no-provider-" + uuid.uuid4().hex)
    claim = queue.claim()
    assert claim and claim.run_id == run_id
    process_claim(queue, claim)
    with engine.connect() as connection:
        terminal = (
            connection.execute(
                text(
                    "SELECT state,terminal_reason_code,progress_json "
                    "FROM epidemic_discovery.discovery_runs WHERE id=:id"
                ),
                {"id": run_id},
            )
            .mappings()
            .one()
        )
    assert terminal["state"] == "NO_CANDIDATE"
    assert terminal["terminal_reason_code"] == "GENERIC_SEARCH_PROVIDER_UNCONFIGURED"
    assert terminal["progress_json"]["qualified"] == 0
