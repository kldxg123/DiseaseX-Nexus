# ruff: noqa: E501
"""Live-DB integration tests for the dual loop (read-only except a single
explicitly rolled-back reconciliation test that never commits).

Run with: PYTHONPATH=runtime/python-packages:src pytest tests/kg_dual_loop -m integration
"""
import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = "USER_HOME/projects/openclaw"
DSN = (f"postgresql://postgres@/epidemic_openclaw"
       f"?host={ROOT}/runtime/postgres-run&port=55432")

RUN_A = "4d9894cc-525d-5dd2-be5a-55fc73a2e443"
RUN_B = "b137a6a7-9153-580b-8d94-5cdca4648c24"
REC_A = "c219de41-ea2e-58d9-86cd-339b03213401"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def conn():
    c = psycopg.connect(DSN, autocommit=True)
    yield c
    c.close()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


class TestPersistedLoop:
    def test_run_a_origins(self, conn):
        n = scalar(conn, "select count(distinct origin_index) from epidemic_kg.kg_shadow_backtest_folds where run_id=%s", (RUN_A,))
        assert n == 12

    def test_run_b_origins(self, conn):
        n = scalar(conn, "select count(distinct origin_index) from epidemic_kg.kg_shadow_backtest_folds where run_id=%s", (RUN_B,))
        assert n == 6

    def test_run_b_seasonal_naive_not_eligible(self, conn):
        n = scalar(conn, """select count(*) from epidemic_kg.kg_shadow_backtest_folds
                            where run_id=%s and adapter_id='seasonal_naive'
                            and status='NOT_ELIGIBLE_INSUFFICIENT_TWO_CYCLES'""", (RUN_B,))
        assert n == 6

    def test_learning_cycles_and_champions(self, conn):
        rows = conn.execute(
            """select c.champion_adapter_id, c.champion_source, c.kg_model_status, c.promotion_applied
               from epidemic_kg.kg_champion_decisions c
               join epidemic_kg.kg_learning_cycles l on l.learning_cycle_id = c.learning_cycle_id
               where l.run_id in (%s,%s)""", (RUN_A, RUN_B)).fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r[3] is False  # never auto-promote
        sources = {r[1] for r in rows}
        assert sources <= {"KG_RECOMMENDED_MODEL", "EMPIRICAL_BASELINE_OVERRIDE"}

    def test_feedback_maturity(self, conn):
        matured_a = scalar(conn, "select count(*) from epidemic_kg.kg_forecast_objective_feedback where run_id=%s and feedback_maturity='MATURED'", (RUN_A,))
        awaiting_b = scalar(conn, "select count(*) from epidemic_kg.kg_forecast_objective_feedback where run_id=%s and feedback_maturity='AWAITING_ACTUAL'", (RUN_B,))
        assert matured_a >= 200
        assert awaiting_b == 4

    def test_no_negative_predictions(self, conn):
        n = scalar(conn, "select count(*) from epidemic_kg.kg_shadow_backtest_predictions where run_id in (%s,%s) and yhat < 0", (RUN_A, RUN_B))
        assert n == 0

    def test_idempotent_reexecution(self, conn):
        from epidemic_core.forecast_model_kg.dual_loop import execute_shadow_run

        result = execute_shadow_run(conn, REC_A)
        assert result["status"] == "IDEMPOTENT_REPLAY"
        assert result["run_id"] == RUN_A


class TestNonMutation:
    def test_protected_table_counts(self, conn):
        assert scalar(conn, "select count(*) from epidemic.normalized_observations") == 366082
        assert scalar(conn, "select count(*) from epidemic_kg.kg_recommendations") == 295
        assert scalar(conn, "select count(*) from epidemic_kg.kg_graph_versions") == 3
        assert scalar(conn, "select count(*) from epidemic.forecast_runs") == 7

    def test_no_promotion_or_pointer_change(self, conn):
        assert scalar(conn, "select count(*) from epidemic_kg.kg_forecast_shadow_runs where promotion_eligible") == 0
        assert scalar(conn, "select count(*) from epidemic_kg.kg_forecast_shadow_runs where active_pointer_changed") == 0
        assert scalar(conn, "select count(*) from epidemic_kg.kg_forecast_shadow_runs where trusted_rows_changed <> 0") == 0


class TestRevisionReconciliation:
    def test_revision_update_supersedes_feedback(self):
        """Whole scenario runs inside one transaction that is rolled back, so
        the formal database is never modified."""
        c = psycopg.connect(DSN)  # NOT autocommit: everything rolls back
        try:
            from epidemic_core.forecast_model_kg.dual_loop import reconcile_feedback

            # pick a matured Peru feedback row and pretend its actual was older
            row = c.execute(
                """select feedback_id::text from epidemic_kg.kg_forecast_objective_feedback
                   where run_id=%s and feedback_maturity='MATURED' limit 1""", (RUN_B,)).fetchone()
            if not row:
                pytest.skip("no matured feedback for B")
            c.execute(
                "update epidemic_kg.kg_forecast_objective_feedback set actual_revision=0 where feedback_id=%s",
                (row[0],))
            result = reconcile_feedback(c, run_id=RUN_B)
            assert result["newly_matured"] >= 1
            assert result["superseded_by_revision"] >= 1
            superseded = c.execute(
                "select count(*) from epidemic_kg.kg_forecast_objective_feedback where feedback_id=%s and feedback_maturity='SUPERSEDED_REVISION'",
                (row[0],)).fetchone()[0]
            assert superseded == 1
        finally:
            c.rollback()
            c.close()
