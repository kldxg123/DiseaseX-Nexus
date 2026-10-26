"""Expose verified candidates as review-ready in reviewer cards.

Revision ID: 0014_p1_8b_review_ready_cards
Revises: 0013_p1_8b_discovery_submit_receipt
"""

from alembic import op

revision = "0014_p1_8b_review_ready_cards"
down_revision = "0013_p1_8b_discovery_submit_receipt"
branch_labels = None
depends_on = None


def _view(effective_status: str) -> str:
    return f"""CREATE OR REPLACE VIEW epidemic_discovery.review_candidate_cards_v2 AS
      SELECT v.*,COALESCE(v.metadata->>'geographic_scope','未说明') geographic_scope,
        v.metadata->>'latest_report_period' latest_report_period,v.metadata->>'update_frequency' update_frequency,
        COALESCE((SELECT count(*) FROM epidemic_discovery.review_mapping_integrity_v2 i
          WHERE i.review_case_id=v.review_case_id),0) mapping_error_count,
        true unsupported_geographic_grain,true approval_blocked,{effective_status} effective_status
      FROM epidemic_discovery.review_cases_v v"""


def upgrade() -> None:
    op.execute(
        _view(
            "CASE WHEN v.state='REVIEW_READY' AND v.candidate_status='REVIEW_READY' "
            "THEN 'REVIEW_READY' ELSE 'NEEDS_IMPLEMENTATION' END"
        )
    )


def downgrade() -> None:
    op.execute(_view("'NEEDS_IMPLEMENTATION'"))
