"""Create versioned Diagnosis storage and reuse references.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnoses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("failure_event_id", sa.Uuid(), nullable=False),
        sa.Column("error_signature_id", sa.Uuid(), nullable=True),
        sa.Column(
            "source",
            sa.Enum("RULE", "AI", "REUSED", name="diagnosis_source", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "validation_status",
            sa.Enum(
                "PASSED",
                "REJECTED",
                name="diagnosis_validation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "classification",
            sa.Enum(
                "DAG_CODE",
                "AIRFLOW_PLATFORM",
                "SOURCE_DATABASE",
                "NETWORK",
                "AUTHENTICATION",
                "AUTHORIZATION",
                "RESOURCE",
                "DATA_QUALITY",
                "EXTERNAL_SYSTEM",
                "CONFIGURATION",
                "UNKNOWN",
                name="error_classification",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_reason", sa.Text(), nullable=True),
        sa.Column("matched_rule", sa.String(length=250), nullable=True),
        sa.Column("extracted_values", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("recommended_actions", sa.JSON(), nullable=True),
        sa.Column(
            "retry_decision",
            sa.Enum(
                "RETRYABLE",
                "NOT_RETRYABLE",
                "UNKNOWN",
                name="retry_decision",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("diagnosis_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("reused_from_diagnosis_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "diagnosis_schema_version >= 1",
            name="ck_diagnoses_schema_version",
        ),
        sa.CheckConstraint(
            "rule_version IS NULL OR rule_version >= 1",
            name="ck_diagnoses_rule_version",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_diagnoses_confidence",
        ),
        sa.CheckConstraint(
            "(source = 'REUSED' AND reused_from_diagnosis_id IS NOT NULL "
            "AND classification IS NULL AND root_cause IS NULL AND confidence IS NULL "
            "AND confidence_reason IS NULL AND matched_rule IS NULL "
            "AND extracted_values IS NULL AND evidence IS NULL "
            "AND recommended_actions IS NULL AND retry_decision IS NULL) OR "
            "(source <> 'REUSED' AND reused_from_diagnosis_id IS NULL "
            "AND classification IS NOT NULL AND confidence IS NOT NULL "
            "AND extracted_values IS NOT NULL AND evidence IS NOT NULL "
            "AND recommended_actions IS NOT NULL AND retry_decision IS NOT NULL)",
            name="ck_diagnoses_reused_shape",
        ),
        sa.ForeignKeyConstraint(
            ["error_signature_id"],
            ["error_signatures.id"],
        ),
        sa.ForeignKeyConstraint(
            ["failure_event_id"],
            ["failure_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reused_from_diagnosis_id"],
            ["diagnoses.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diagnoses_reuse_lookup",
        "diagnoses",
        ["error_signature_id", "validation_status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_diagnoses_reuse_lookup", table_name="diagnoses")
    op.drop_table("diagnoses")
