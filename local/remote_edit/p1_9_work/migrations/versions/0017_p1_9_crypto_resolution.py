"""Resolve P1-9 review hashes inside the restricted function search path.

Revision ID: 0017_p1_9_crypto_resolution
Revises: 0016_p1_9_reviewed_promotion
"""

from alembic import op

revision = "0017_p1_9_crypto_resolution"
down_revision = "0016_p1_9_reviewed_promotion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION epidemic_discovery.digest(text,text) RETURNS bytea
        LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path=pg_catalog
        AS $function$
        BEGIN
          IF lower($2) <> 'sha256' THEN
            RAISE EXCEPTION 'unsupported digest algorithm';
          END IF;
          RETURN pg_catalog.sha256(pg_catalog.convert_to($1,'UTF8'));
        END
        $function$"""
    )
    op.execute("REVOKE ALL ON FUNCTION epidemic_discovery.digest(text,text) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS epidemic_discovery.digest(text,text)")
