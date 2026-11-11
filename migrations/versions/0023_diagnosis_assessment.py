"""Add evidence assessment without reinterpreting historical diagnoses."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diagnoses", sa.Column("assessment", sa.JSON(none_as_null=True), nullable=True))
    with op.batch_alter_table("diagnoses") as batch:
        batch.create_check_constraint(
            "ck_diagnoses_reused_assessment", "source <> 'REUSED' OR assessment IS NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("diagnoses") as batch:
        batch.drop_constraint("ck_diagnoses_reused_assessment", type_="check")
        batch.drop_column("assessment")
