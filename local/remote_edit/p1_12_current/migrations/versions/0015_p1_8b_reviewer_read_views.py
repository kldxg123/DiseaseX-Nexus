"""Complete least-privilege reviewer read views.

Revision ID: 0015_p1_8b_reviewer_read_views
Revises: 0014_p1_8b_review_ready_cards
"""

from alembic import op

revision = "0015_p1_8b_reviewer_read_views"
down_revision = "0014_p1_8b_review_ready_cards"
branch_labels = None
depends_on = None


def _view(*, verified: bool) -> str:
    extra = ",progress_json,completed_at" if verified else ""
    return f"""CREATE VIEW epidemic_discovery.review_discovery_runs_v2 AS
      SELECT id discovery_run_id,state,state_version,request_json,policy_version,created_at,updated_at{extra},
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc
          WHERE sc.discovery_run_id=dr.id) candidate_count,
        (SELECT count(*) FROM epidemic_discovery.source_candidates sc
          WHERE sc.discovery_run_id=dr.id AND sc.status='REVIEW_READY') review_ready_count
      FROM epidemic_discovery.discovery_runs dr"""


def _replace(*, verified: bool) -> None:
    op.execute("DROP VIEW epidemic_discovery.review_discovery_runs_v2")
    op.execute(_view(verified=verified))
    op.execute("GRANT SELECT ON epidemic_discovery.review_discovery_runs_v2 TO epidemicreview")


def upgrade() -> None:
    _replace(verified=True)


def downgrade() -> None:
    _replace(verified=False)
