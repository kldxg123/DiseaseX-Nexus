from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from epidemic_core.discovery.providers import (
    BraveSearchProvider,
    DirectoryRegistry,
    ProviderCapability,
    ProviderOutcome,
    RequestEvidence,
    SearchResult,
)
from epidemic_core.discovery.safe_fetcher import FetchResult
from epidemic_core.discovery.service import _profile_csv
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
    started = time.monotonic()
    process_claim(
        queue,
        claim,
        providers=(BraveSearchProvider(key="", enabled=True),),
        registry=DirectoryRegistry("fixture-empty-v1", ()),
    )
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
    assert time.monotonic() - started < 10
    assert terminal["state"] == "FAILED_RETRYABLE"
    assert terminal["terminal_reason_code"] is None
    assert terminal["progress_json"]["qualified_candidates"] == 0
    assert terminal["progress_json"]["network_requests"] == 0


class FixtureProvider:
    def __init__(self, results: tuple[SearchResult, ...]):
        self.results = results

    def capability(self, required_channel: bool) -> ProviderCapability:
        return ProviderCapability(
            "fixture-provider-v1",
            "RECORDED_FIXTURE_API",
            True,
            True,
            "READY",
            False,
            ("*",),
            ("*",),
            None,
            None,
            True,
        )

    def search(self, request, required_channel: bool) -> ProviderOutcome:
        return ProviderOutcome(
            self.capability(required_channel),
            RequestEvidence(
                "fixture-provider-v1",
                "fixture-directory-v1",
                "Recorded fixture directory",
                request.country_iso3,
                "fixtures.example",
                request.summary(),
                "1" * 64,
                True,
                200,
                2,
                len(self.results),
                "2" * 64,
                "SUCCESS" if self.results else "NO_RESULTS",
                None,
                True,
            ),
            self.results,
        )


class CsvFixtureFetcher:
    def __init__(self, marker: str):
        self.payload = (
            f"period_start,country,cases_new,deaths_new,incidence_rate,note\n"
            f"2026-01-05,CHN,10,1,0.7,{marker}\n"
            f"2026-01-12,CHN,12,0,0.8,{marker}\n"
        ).encode()

    def fetch(self, url: str) -> FetchResult:
        digest = hashlib.sha256(self.payload).hexdigest()
        return FetchResult(
            url,
            hashlib.sha256(url.encode()).hexdigest(),
            self.payload,
            "text/csv",
            (),
            "fixtures.example",
            200,
            3,
            digest,
        )


def _formal_counts(engine) -> tuple[int, ...]:
    tables = (
        "data_sources",
        "query_requests",
        "retrieval_runs",
        "source_fetches",
        "raw_artifacts",
        "normalized_observations",
        "dataset_snapshots",
    )
    with engine.connect() as connection:
        return tuple(
            connection.execute(text(f"SELECT count(*) FROM epidemic.{table}")).scalar_one() for table in tables
        )


def test_fixture_candidate_is_quarantined_for_review_without_formal_writes(engine, disease_id, tmp_path) -> None:
    marker = uuid.uuid4().hex
    run_id = _submit(engine, disease_id, "fixture-candidate-" + marker)
    queue = Queue(engine, "fixture-candidate-worker", dt.datetime.now(dt.timezone.utc))
    claim = queue.claim()
    assert claim and claim.run_id == run_id
    before = _formal_counts(engine)
    provider = FixtureProvider(
        (
            SearchResult(
                "https://fixtures.example/china-covid-weekly.csv",
                "China COVID-19 weekly fixture",
                "Fixture public-health authority",
                "Recorded integration fixture",
                "NATIONAL_HEALTH",
                "text/csv",
                "CUSTOM_REVIEW_REQUIRED",
            ),
            SearchResult(
                "https://fixtures.example/china-covid-weekly.csv",
                "Duplicate fixture result",
                "Fixture public-health authority",
                "Duplicate recorded result",
                "NATIONAL_HEALTH",
                "text/csv",
                "CUSTOM_REVIEW_REQUIRED",
            ),
        )
    )
    process_claim(
        queue,
        claim,
        providers=(provider,),
        registry=DirectoryRegistry("fixture-registry-v1", ()),
        candidate_fetcher=CsvFixtureFetcher(marker),
        quarantine_root=tmp_path,
        candidate_profile_override=_profile_csv(CsvFixtureFetcher(marker).payload),
    )
    with engine.connect() as connection:
        state = connection.execute(
            text("SELECT state,terminal_reason_code FROM epidemic_discovery.discovery_runs WHERE id=:id"),
            {"id": run_id},
        ).one()
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM epidemic_discovery.source_candidates WHERE discovery_run_id=:id),"
                "(SELECT count(*) FROM epidemic_discovery.source_review_cases rc "
                "JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id "
                "JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id "
                "WHERE c.discovery_run_id=:id),"
                "(SELECT count(*) FROM epidemic_discovery.source_review_decisions d "
                "JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id "
                "JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id "
                "JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id "
                "WHERE c.discovery_run_id=:id),"
                "(SELECT count(*) FROM epidemic_discovery.source_promotion_runs)"
            ),
            {"id": run_id},
        ).one()
    assert state == ("REVIEW_READY", "CANDIDATES_AWAITING_HUMAN_REVIEW")
    assert counts[0:3] == (1, 1, 0)
    assert _formal_counts(engine) == before


def test_configured_zero_provider_is_complete_no_candidate(engine, disease_id) -> None:
    run_id = _submit(engine, disease_id, "fixture-zero-" + uuid.uuid4().hex)
    queue = Queue(engine, "fixture-zero-worker", dt.datetime.now(dt.timezone.utc))
    claim = queue.claim()
    assert claim and claim.run_id == run_id
    process_claim(
        queue,
        claim,
        providers=(FixtureProvider(()),),
        registry=DirectoryRegistry("fixture-registry-v1", ()),
    )
    with engine.connect() as connection:
        row = (
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
    assert row["state"] == "DISCOVERY_COMPLETE_NO_CANDIDATE"
    assert row["terminal_reason_code"] == "DISCOVERY_COMPLETE_NO_CANDIDATE"
    assert row["progress_json"]["network_requests"] == 1
    assert row["progress_json"]["successful_responses"] == 1
