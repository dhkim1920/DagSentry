"""Add immutable operator-authored Incident diagnosis revisions.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_human_diagnoses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "PUBLISH",
                "WITHDRAW",
                name="human_diagnosis_action",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("basis_diagnosis_id", sa.Uuid(), nullable=True),
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
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("recommended_actions", sa.JSON(), nullable=True),
        sa.Column(
            "retry_decision",
            sa.Enum(
                "RETRYABLE",
                "NOT_RETRYABLE",
                "UNKNOWN",
                name="retry_decision",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_identity", sa.String(length=250), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("revision >= 1", name="ck_incident_human_diagnoses_revision"),
        sa.CheckConstraint(
            "(action = 'PUBLISH' AND classification IS NOT NULL AND root_cause IS NOT NULL "
            "AND recommended_actions IS NOT NULL AND retry_decision IS NOT NULL) OR "
            "(action = 'WITHDRAW' AND classification IS NULL AND root_cause IS NULL "
            "AND recommended_actions IS NULL AND retry_decision IS NULL "
            "AND basis_diagnosis_id IS NULL)",
            name="ck_incident_human_diagnoses_shape",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["incident_human_diagnoses.id"]),
        sa.ForeignKeyConstraint(["basis_diagnosis_id"], ["diagnoses.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "revision", name="uq_incident_human_diagnoses_revision"),
    )
    op.create_index(
        "ix_incident_human_diagnoses_incident_revision",
        "incident_human_diagnoses",
        ["incident_id", "revision"],
    )
    op.create_table(
        "incident_human_diagnosis_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("human_diagnosis_id", sa.Uuid(), nullable=False),
        sa.Column("source_diagnosis_id", sa.Uuid(), nullable=False),
        sa.Column("failure_event_id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("line_id >= 1", name="ck_incident_human_diagnosis_evidence_line"),
        sa.CheckConstraint("position >= 0", name="ck_incident_human_diagnosis_evidence_position"),
        sa.ForeignKeyConstraint(
            ["human_diagnosis_id"], ["incident_human_diagnoses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_diagnosis_id"], ["diagnoses.id"]),
        sa.ForeignKeyConstraint(["failure_event_id"], ["failure_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "human_diagnosis_id",
            "source_diagnosis_id",
            "line_id",
            name="uq_incident_human_diagnosis_evidence_source",
        ),
        sa.UniqueConstraint(
            "human_diagnosis_id", "position", name="uq_incident_human_diagnosis_evidence_position"
        ),
    )


def downgrade() -> None:
    op.drop_table("incident_human_diagnosis_evidence")
    op.drop_index(
        "ix_incident_human_diagnoses_incident_revision", table_name="incident_human_diagnoses"
    )
    op.drop_table("incident_human_diagnoses")
