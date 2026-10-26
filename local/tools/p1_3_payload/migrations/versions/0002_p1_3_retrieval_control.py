"""P1-3 retrieval claim fencing and response metadata.

Revision ID: 0002_p1_3_retrieval_control
Revises: 0001_p1_1
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_p1_3_retrieval_control"
down_revision: str | None = "0001_p1_1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("retrieval_runs",sa.Column("claim_token",sa.Uuid(),nullable=True),schema="epidemic")
    op.add_column("retrieval_runs",sa.Column("claimed_at",sa.DateTime(timezone=True),nullable=True),schema="epidemic")
    op.add_column("retrieval_runs",sa.Column("lease_expires_at",sa.DateTime(timezone=True),nullable=True),schema="epidemic")
    op.add_column("retrieval_runs",sa.Column("next_attempt_at",sa.DateTime(timezone=True),nullable=True),schema="epidemic")
    op.create_check_constraint(
        "ck_retrieval_runs_claim_shape","retrieval_runs",
        "(status='running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL "
        "AND lease_expires_at IS NOT NULL) OR (status<>'running' AND claim_token IS NULL)",schema="epidemic")
    op.create_index("ix_retrieval_runs_pending_queue","retrieval_runs",["created_at"],unique=False,schema="epidemic",postgresql_where=sa.text("status='pending'"))
    op.add_column("source_fetches",sa.Column("response_content_type",sa.String(length=255),nullable=True),schema="epidemic")
    op.add_column("source_fetches",sa.Column("response_byte_size",sa.BigInteger(),nullable=True),schema="epidemic")
    op.create_check_constraint("ck_source_fetches_response_byte_size","source_fetches","response_byte_size IS NULL OR response_byte_size >= 0",schema="epidemic")
    op.execute(
        "GRANT UPDATE(attempt_number,status,started_at,finished_at,error_code,error_message,"
        "claim_token,claimed_at,lease_expires_at,next_attempt_at) ON epidemic.retrieval_runs TO epidemic_app")


def downgrade() -> None:
    op.execute(
        "REVOKE UPDATE(attempt_number,claim_token,claimed_at,lease_expires_at,next_attempt_at) "
        "ON epidemic.retrieval_runs FROM epidemic_app")
    op.drop_constraint("ck_source_fetches_response_byte_size","source_fetches",schema="epidemic",type_="check")
    op.drop_column("source_fetches","response_byte_size",schema="epidemic")
    op.drop_column("source_fetches","response_content_type",schema="epidemic")
    op.drop_index("ix_retrieval_runs_pending_queue",table_name="retrieval_runs",schema="epidemic")
    op.drop_constraint("ck_retrieval_runs_claim_shape","retrieval_runs",schema="epidemic",type_="check")
    op.drop_column("retrieval_runs","next_attempt_at",schema="epidemic")
    op.drop_column("retrieval_runs","lease_expires_at",schema="epidemic")
    op.drop_column("retrieval_runs","claimed_at",schema="epidemic")
    op.drop_column("retrieval_runs","claim_token",schema="epidemic")
