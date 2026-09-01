"""Add AI validation details to Diagnosis storage.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "diagnoses",
        sa.Column("operator_review_required", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "diagnoses",
        sa.Column("validation_errors", sa.JSON(), nullable=True),
    )
    op.drop_constraint("ck_diagnoses_reused_shape", "diagnoses", type_="check")
    op.create_check_constraint(
        "ck_diagnoses_reused_shape",
        "diagnoses",
        "(source = 'REUSED' AND reused_from_diagnosis_id IS NOT NULL "
        "AND classification IS NULL AND root_cause IS NULL AND confidence IS NULL "
        "AND confidence_reason IS NULL AND matched_rule IS NULL "
        "AND extracted_values IS NULL AND evidence IS NULL "
        "AND recommended_actions IS NULL AND retry_decision IS NULL "
        "AND operator_review_required IS NULL AND validation_errors IS NULL) OR "
        "(source <> 'REUSED' AND reused_from_diagnosis_id IS NULL "
        "AND classification IS NOT NULL AND confidence IS NOT NULL "
        "AND extracted_values IS NOT NULL AND evidence IS NOT NULL "
        "AND recommended_actions IS NOT NULL AND retry_decision IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_diagnoses_reused_shape", "diagnoses", type_="check")
    op.create_check_constraint(
        "ck_diagnoses_reused_shape",
        "diagnoses",
        "(source = 'REUSED' AND reused_from_diagnosis_id IS NOT NULL "
        "AND classification IS NULL AND root_cause IS NULL AND confidence IS NULL "
        "AND confidence_reason IS NULL AND matched_rule IS NULL "
        "AND extracted_values IS NULL AND evidence IS NULL "
        "AND recommended_actions IS NULL AND retry_decision IS NULL) OR "
        "(source <> 'REUSED' AND reused_from_diagnosis_id IS NULL "
        "AND classification IS NOT NULL AND confidence IS NOT NULL "
        "AND extracted_values IS NOT NULL AND evidence IS NOT NULL "
        "AND recommended_actions IS NOT NULL AND retry_decision IS NOT NULL)",
    )
    op.drop_column("diagnoses", "validation_errors")
    op.drop_column("diagnoses", "operator_review_required")
