"""KG P1 paper1 quarantine marker migration.

Revision ID: 0031_kg_p1_paper1_quarantine
Revises: 0030_fasttrack_correctness_repair
Create Date: 2026-08-02
"""

from __future__ import annotations


revision = "0031_kg_p1_paper1_quarantine"
down_revision = "0030_fasttrack_correctness_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Schema objects are applied by the KG P1 guarded deployment script."""
    return None


def downgrade() -> None:
    """Downgrade is intentionally handled by the guarded deployment script."""
    return None
