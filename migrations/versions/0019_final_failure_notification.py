"""Reserve one final failed event per Incident."""

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.add_column(sa.Column("final_failure_event_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_incidents_final_failure_event",
            "failure_events",
            ["final_failure_event_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        "UPDATE incidents SET final_failure_event_id = initial_failure_event_id "
        "WHERE EXISTS (SELECT 1 FROM failure_events "
        "WHERE failure_events.id = incidents.initial_failure_event_id "
        "AND failure_events.state = 'FAILED')"
    )


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.drop_constraint("fk_incidents_final_failure_event", type_="foreignkey")
        batch.drop_column("final_failure_event_id")
