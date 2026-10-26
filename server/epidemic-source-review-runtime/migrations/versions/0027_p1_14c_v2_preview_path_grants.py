"""Grant the v2 review-ready gate check to the candidate runtime writer.

Revision ID: 0027_p1_14c_v2_preview_path_grants
Revises: 0026_p1_12_preview_view_grants
"""

from alembic import op

revision = "0027_p1_14c_v2_preview_path_grants"
down_revision = "0026_p1_12_preview_view_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT EXECUTE ON FUNCTION epidemic_p1_12.review_ready_v2_violations(uuid) TO epidemic_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION epidemic_p1_12.review_ready_v2_violations(uuid) FROM epidemic_app"
    )
