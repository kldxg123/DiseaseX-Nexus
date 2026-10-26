# ruff: noqa: E501
"""Live-DB integration tests for the parameter-tuning demo (kg_pt).

Cannot run locally: requires the vm201 postgres unix socket, migration 0041
applied, and the real AUT source series. Run remotely with:
    PYTHONPATH=runtime/python-packages:src pytest tests/kg_pt -m integration
"""

import threading

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = "USER_HOME/projects/openclaw"
DSN = f"postgresql://postgres@/epidemic_openclaw?host={ROOT}/runtime/postgres-run&port=55432"
APP_DSN = f"postgresql://epidemic_app@/epidemic_openclaw?host={ROOT}/runtime/postgres-run&port=55432"

IDEM = "pt-demo:AUT:legionella:v1"
IDEM_CONC = "pt-demo:AUT:legionella:v1:concurrency-probe"
IDEM_SECOND = "pt-demo:AUT:legionella:v1:second-key-probe"

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "kg_pt_demo_snapshots",
    "kg_pt_demo_snapshot_rows",
    "kg_pt_temporal_splits",
    "kg_pt_parameter_contracts",
    "kg_pt_tuning_runs",
    "kg_pt_tuning_trials",
    "kg_pt_trial_origins",
    "kg_pt_test_evaluations",
}


@pytest.fixture(scope="module")
def conn():
    c = psycopg.connect(DSN, autocommit=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def demo_result():
    """Execute the demo once as epidemic_app (proves the app role can run the
    whole pipeline end-to-end) and hand the result dict to all tests."""
    from epidemic_core.kg_pt.pipeline import execute_pt_demo

    c = psycopg.connect(APP_DSN, autocommit=True)
    try:
        return execute_pt_demo(c, idempotency_key=IDEM)
    finally:
        c.close()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


class TestMigration:
    def test_pt_tables_exist(self, conn):
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema='epidemic_kg'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert names >= EXPECTED_TABLES


class TestExecuteDemo:
    def test_first_execution_completed(self, demo_result):
        assert demo_result["status"] in {"COMPLETED", "IDEMPOTENT_REPLAY"}
        assert demo_result["tuning_run_id"]
        assert demo_result["shadow_run_id"]

    def test_second_execution_is_idempotent_replay_with_same_ids(self, demo_result):
        from epidemic_core.kg_pt.pipeline import execute_pt_demo

        c = psycopg.connect(APP_DSN, autocommit=True)
        try:
            again = execute_pt_demo(c, idempotency_key=IDEM)
        finally:
            c.close()
        assert again["status"] == "IDEMPOTENT_REPLAY"
        assert again["tuning_run_id"] == demo_result["tuning_run_id"]
        assert again["tuning_run_id_cycle2"] == demo_result["tuning_run_id_cycle2"]
        assert again["shadow_run_id"] == demo_result["shadow_run_id"]
        assert again["snapshot_sha256"] == demo_result["snapshot_sha256"]

    def test_different_key_same_content_converges_without_writes(self, conn, demo_result):
        """A DIFFERENT idempotency key with identical content must replay the
        existing run ids and write nothing (regression test for the
        kg_forecast_shadow_runs_run_key_key UniqueViolation)."""
        from epidemic_core.kg_pt.pipeline import execute_pt_demo

        before_runs = scalar(conn, "select count(*) from epidemic_kg.kg_pt_tuning_runs")
        before_evals = scalar(conn, "select count(*) from epidemic_kg.kg_pt_test_evaluations")
        before_shadow = scalar(
            conn, "select count(*) from epidemic_kg.kg_forecast_shadow_runs where run_key like 'pt-demo:%%'"
        )
        c = psycopg.connect(APP_DSN, autocommit=True)
        try:
            r = execute_pt_demo(c, idempotency_key=IDEM_SECOND)
        finally:
            c.close()
        assert r["status"] == "IDEMPOTENT_REPLAY"
        assert r["tuning_run_id"] == demo_result["tuning_run_id"]
        assert r["tuning_run_id_cycle2"] == demo_result["tuning_run_id_cycle2"]
        assert r["shadow_run_id"] == demo_result["shadow_run_id"]
        assert scalar(conn, "select count(*) from epidemic_kg.kg_pt_tuning_runs") == before_runs
        assert scalar(conn, "select count(*) from epidemic_kg.kg_pt_test_evaluations") == before_evals
        assert (
            scalar(conn, "select count(*) from epidemic_kg.kg_forecast_shadow_runs where run_key like 'pt-demo:%%'")
            == before_shadow
        )

    def test_concurrent_runners_converge_to_single_run(self, conn, demo_result):
        from epidemic_core.kg_pt.pipeline import execute_pt_demo

        results: list[dict] = []
        errors: list[Exception] = []

        def worker():
            try:
                c = psycopg.connect(APP_DSN, autocommit=True)
                try:
                    results.append(execute_pt_demo(c, idempotency_key=IDEM_CONC))
                finally:
                    c.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(results) == 8
        # all callers converge on one run — the pre-existing content twin
        assert {r["tuning_run_id"] for r in results} == {demo_result["tuning_run_id"]}
        assert {r["shadow_run_id"] for r in results} == {demo_result["shadow_run_id"]}
        # no duplicate content-identical tuning runs anywhere
        dup = scalar(
            conn,
            "select count(*) from (select snapshot_sha256, split_sha256, adapter_id, cycle_index, count(*) c "
            "from epidemic_kg.kg_pt_tuning_runs group by 1,2,3,4 having count(*) > 1) d",
        )
        assert dup == 0


class TestOutcomes:
    def test_exactly_five_test_evaluations_per_tuning_run(self, conn, demo_result):
        n = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_pt_test_evaluations where tuning_run_id=%s",
            (demo_result["tuning_run_id"],),
        )
        assert n == 5
        adapters = conn.execute(
            "select adapter_id, is_tuned_model from epidemic_kg.kg_pt_test_evaluations where tuning_run_id=%s",
            (demo_result["tuning_run_id"],),
        ).fetchall()
        tuned = [a for a, is_tuned in adapters if is_tuned]
        assert tuned == ["negbin_dglm.v1"]
        assert {a for a, _ in adapters} == {
            "negbin_dglm.v1",
            "last_value",
            "rolling_mean",
            "drift",
            "seasonal_naive",
        }

    def test_repeated_execution_does_not_add_evaluations(self, conn, demo_result):
        from epidemic_core.kg_pt.pipeline import execute_pt_demo

        before = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_pt_test_evaluations where tuning_run_id=%s",
            (demo_result["tuning_run_id"],),
        )
        c = psycopg.connect(APP_DSN, autocommit=True)
        try:
            execute_pt_demo(c, idempotency_key=IDEM)
        finally:
            c.close()
        after = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_pt_test_evaluations where tuning_run_id=%s",
            (demo_result["tuning_run_id"],),
        )
        assert before == after == 5

    def test_feedback_maturity_counts(self, conn, demo_result):
        run_id = demo_result["shadow_run_id"]
        matured = scalar(
            conn,
            """select count(*) from epidemic_kg.kg_forecast_objective_feedback f
               join epidemic_kg.kg_forecast_shadow_values v on v.value_id = f.forecast_value_id
               where f.run_id=%s and f.feedback_maturity='MATURED' and v.row_type='TEST_REPLAY'""",
            (run_id,),
        )
        awaiting = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_forecast_objective_feedback "
            "where run_id=%s and feedback_maturity='AWAITING_ACTUAL'",
            (run_id,),
        )
        superseded = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_forecast_objective_feedback "
            "where run_id=%s and feedback_maturity='SUPERSEDED_REVISION'",
            (run_id,),
        )
        assert matured == 36  # 9 test origins x H1-H4, matured via DEMO_HISTORICAL_REPLAY
        assert awaiting == 4  # only the future H1-H4 (real actuals unknown)
        assert superseded == 36

    def test_learning_cycles_and_champion_decisions(self, conn, demo_result):
        rows = conn.execute(
            """select l.cycle_type, c.champion_adapter_id, c.champion_source, c.kg_model_status, c.promotion_applied
               from epidemic_kg.kg_learning_cycles l
               join epidemic_kg.kg_champion_decisions c on c.learning_cycle_id = l.learning_cycle_id
               where l.run_id=%s""",
            (demo_result["shadow_run_id"],),
        ).fetchall()
        assert {r[0] for r in rows} == {"PARAMETER_TUNING_EVALUATION", "CONTROLLED_RETRAIN_HISTORICAL_REPLAY"}
        for r in rows:
            assert r[4] is False  # promotion_applied: never
            assert r[2] in {"KG_RECOMMENDED_MODEL", "EMPIRICAL_BASELINE_OVERRIDE"}
        replay_evidence = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_learning_cycles where run_id=%s "
            "and evidence->>'replay' = 'DEMO_HISTORICAL_REPLAY'",
            (demo_result["shadow_run_id"],),
        )
        assert replay_evidence == 2
        heldout = scalar(
            conn,
            "select count(*) from epidemic_kg.kg_learning_cycles where run_id=%s "
            "and cycle_type='CONTROLLED_RETRAIN_HISTORICAL_REPLAY' "
            "and evidence->>'heldout' = 'NO_REMAINING_HELDOUT'",
            (demo_result["shadow_run_id"],),
        )
        assert heldout == 1

    def test_shadow_run_flags(self, conn, demo_result):
        row = conn.execute(
            "select mode, promotion_eligible, active_pointer_changed, trusted_rows_changed "
            "from epidemic_kg.kg_forecast_shadow_runs where run_id=%s",
            (demo_result["shadow_run_id"],),
        ).fetchone()
        assert row[0] == "SHADOW"
        assert row[1] is False and row[2] is False and row[3] == 0


class TestNonMutation:
    def test_protected_table_counts(self, conn):
        assert scalar(conn, "select count(*) from epidemic.normalized_observations") == 366082
        assert scalar(conn, "select count(*) from epidemic_kg.kg_recommendations") == 295
        assert scalar(conn, "select count(*) from epidemic_kg.kg_graph_versions") == 3
        assert scalar(conn, "select count(*) from epidemic.forecast_runs") == 7

    def test_snapshot_rows_immutable_at_db_level(self):
        """epidemic_app has no UPDATE grant on kg_pt_demo_snapshot_rows."""
        c = psycopg.connect(APP_DSN)  # NOT autocommit: rolled back below
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("update epidemic_kg.kg_pt_demo_snapshot_rows set value_numeric = 0 where true")
        finally:
            c.rollback()
            c.close()
