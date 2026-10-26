"""Restore preview view grants for dashboard readiness.

Revision ID: 0026_p1_12_preview_view_grants
Revises: 0025_p1_12_restore_preview_dashboard_views
"""

from alembic import op

revision = "0026_p1_12_preview_view_grants"
down_revision = "0025_p1_12_restore_preview_dashboard_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """DO $$BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_dashboard_ro') THEN
          GRANT USAGE ON SCHEMA epidemic_preview TO epidemic_dashboard_ro;
          GRANT SELECT ON epidemic_preview.active_preview_publications_v1,
            epidemic_preview.preview_dashboard_series_v1,
            epidemic_preview.preview_lineage_v1 TO epidemic_dashboard_ro;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemicreview') THEN
          GRANT USAGE ON SCHEMA epidemic_preview,epidemic_p1_12 TO epidemicreview;
          GRANT SELECT ON epidemic_preview.preview_review_cards_v1 TO epidemicreview;
        END IF;
        END$$"""
    )


def downgrade() -> None:
    pass
