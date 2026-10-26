"""Live-DB machine-gate verification tests (read-only against epidemic_openclaw).
Run at the end of the pipeline; every test asserts a gate condition from the
task contract. These tests never write to the database.
"""
import json
import os

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = "USER_HOME/projects/openclaw"
DSN = (f"postgresql://postgres@/epidemic_openclaw"
       f"?host={ROOT}/runtime/postgres-run&port=55432")
IMPORT_RUN_ID = os.environ.get("KG_R3C_IMPORT_RUN_ID",
                               "bef74d0c-f60e-58d8-af98-d646a6401958")
PARENT_RUN = "9cdc3d93-3155-523e-887f-cd6068196b08"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def conn():
    c = psycopg.connect(DSN, autocommit=True)
    yield c
    c.close()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


class TestImportAccounting:
    def test_import_run_exists(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_import_runs WHERE import_run_id=%s",
                      (IMPORT_RUN_ID,)) == 1

    def test_all_members_accounted(self, conn):
        n = scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_import_members WHERE import_run_id=%s",
                   (IMPORT_RUN_ID,))
        assert n == 1693

    def test_quarantine_accounted(self, conn):
        n = scalar(conn, """SELECT count(*) FROM epidemic_kg.kg_import_members
                            WHERE import_run_id=%s AND quarantined""", (IMPORT_RUN_ID,))
        assert n >= 10  # 10 unexpected-type text members

    def test_archive_sha256_recorded(self, conn):
        sha = scalar(conn, "SELECT archive_sha256 FROM epidemic_kg.kg_import_runs WHERE import_run_id=%s",
                     (IMPORT_RUN_ID,))
        assert sha == "64778b3070215d9fc08311c1e8303270565ebac8a267675f874dcdb21764dbe7"

    def test_metadata_rows_accounted(self, conn):
        n = scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_paper_works")
        assert n >= 1600

    def test_utf8_no_corruption(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_paper_works WHERE title ~ '\uFFFD'") == 0


class TestGraphVersion:
    def test_graph_version_published(self, conn):
        n = scalar(conn, """SELECT count(*) FROM epidemic_kg.kg_graph_versions
                            WHERE import_run_id=%s""", (IMPORT_RUN_ID,))
        assert n >= 1

    def test_orphan_evidence_zero(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_evidence_spans s
            WHERE s.properties->>'extractor' = 'kg_r3c.evidence_extractor'
              AND NOT EXISTS (SELECT 1 FROM epidemic_kg.kg_assertion_evidence ae
                              WHERE ae.evidence_span_id = s.evidence_span_id)""")
        assert n == 0

    def test_assertion_set_hash_nonempty(self, conn):
        h = scalar(conn, """SELECT assertion_set_hash FROM epidemic_kg.kg_graph_versions
                            WHERE import_run_id=%s ORDER BY published_at DESC LIMIT 1""",
                   (IMPORT_RUN_ID,))
        assert h and len(h) == 64


class TestHistoricalPreservation:
    def test_legacy_claims_count(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_claims") >= 59

    def test_legacy_work_nodes_kept(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_nodes WHERE node_type='WORK'") >= 146

    def test_parent_run_intact(self, conn):
        row = conn.execute("""SELECT score_type, actually_executed, fit_called, predict_called
                              FROM epidemic_kg.kg_preexecution_scoring_runs WHERE run_id=%s""",
                           (PARENT_RUN,)).fetchone()
        assert row is not None
        assert row[0] == "PRE_EXECUTION_EVIDENCE_SCORE"
        assert row[1] is False and row[2] is False and row[3] is False

    def test_r3a_candidates_intact(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_preexecution_model_candidates") == 26

    def test_r3a_components_intact(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_preexecution_score_components") == 104


class TestScoringIntegrity:
    def test_no_baseline_recommendations(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_recommendations r
            JOIN epidemic_kg.kg_recommendation_runs rr ON rr.run_id = r.run_id
            WHERE r.is_baseline AND r.eligibility_status = 'ELIGIBLE_RECOMMENDATION'""")
        assert n == 0

    def test_eligible_means_both_above_70(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_recommendations
            WHERE eligibility_status = 'ELIGIBLE_RECOMMENDATION'
              AND NOT (total_score > 70 AND parameter_confidence_score > 70)""")
        assert n == 0

    def test_stored_equals_recomputed(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_recommendations
            WHERE total_score IS NOT NULL
              AND (abs(total_score - recomputed_total) > 1e-6
                   OR abs(parameter_confidence_score - recomputed_param) > 1e-6)""")
        assert n == 0

    def test_d_components_have_sources(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_recommendation_components
            WHERE dimension = 'D_scientific_evidence' AND score > 0
              AND (source_ids = '[]'::jsonb
                   OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(source_ids) e
                              WHERE e IS NULL OR e = ''))""")
        assert n == 0

    def test_score_type_label(self, conn):
        st = scalar(conn, """SELECT score_type FROM epidemic_kg.kg_recommendation_runs
                             ORDER BY created_at DESC LIMIT 1""")
        assert st == "PRE_EXECUTION_EVIDENCE_SCORE"

    def test_execution_deferred(self, conn):
        row = conn.execute("""SELECT actually_executed, forecast_run_id, execution_status
                              FROM epidemic_kg.kg_recommendation_runs
                              ORDER BY created_at DESC LIMIT 1""").fetchone()
        assert row[0] is False and row[1] is None and row[2] == "DEFERRED_NOT_EXECUTED"

    def test_bundles_complete(self, conn):
        n = scalar(conn, """SELECT count(*) FROM epidemic_kg.kg_parameter_bundles
                            WHERE completeness < 1.0""")
        assert n == 0

    def test_no_placeholder_param_values(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_parameter_bundles b
            WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(b.parameters) p
                          WHERE p->>'value' IN ('TBD','TODO','registry_default',''))""")
        assert n == 0


class TestProfileCoverage:
    def test_26_profiles(self, conn):
        assert scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_data_profiles") == 26

    def test_every_profile_has_nonbaseline_candidate_rows(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_data_profiles p
            WHERE NOT EXISTS (
              SELECT 1 FROM epidemic_kg.kg_recommendations r
              WHERE r.profile_id = p.profile_id AND NOT r.is_baseline)""")
        assert n == 0

    def test_cumulative_not_treated_as_incident(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM epidemic_kg.kg_recommendations r
            JOIN epidemic_kg.kg_data_profiles p ON p.profile_id = r.profile_id
            JOIN epidemic_kg.kg_model_adapters a ON a.adapter_id = r.adapter_id
            WHERE p.profile_json->>'semantics' = 'CUMULATIVE'
              AND r.eligibility_status = 'ELIGIBLE_RECOMMENDATION'
              AND NOT (a.capabilities->'semantics' ? 'CUMULATIVE')""")
        assert n == 0


class TestIdempotencyReplay:
    def test_reimport_same_zip_no_duplicates(self, conn):
        # stable ids + unique constraints: member registry must not exceed 1693
        n = scalar(conn, "SELECT count(*) FROM epidemic_kg.kg_import_members WHERE import_run_id=%s",
                   (IMPORT_RUN_ID,))
        assert n == 1693

    def test_unique_fulltext_sha256(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM (
              SELECT fulltext_sha256, count(*) c FROM epidemic_kg.kg_document_versions
              GROUP BY 1 HAVING count(*) > 1) t""")
        assert n == 0

    def test_recommendation_run_single_per_input(self, conn):
        n = scalar(conn, """
            SELECT count(*) FROM (
              SELECT graph_version_id, policy_id, profile_set_hash, count(*) c
              FROM epidemic_kg.kg_recommendation_runs GROUP BY 1,2,3 HAVING count(*) > 1) t""")
        assert n == 0
