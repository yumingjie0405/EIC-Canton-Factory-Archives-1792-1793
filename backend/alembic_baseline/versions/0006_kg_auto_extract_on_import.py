"""Auto research on import: group switch plus reliable queue delivery bookkeeping.

Two independent facts are recorded here:

* ``research_groups.auto_extract`` — may *this group* take part in automatic
  knowledge-graph research when a Word import succeeds? It never starts anything by
  itself (the deployment master switch does that), and turning it on must not
  retroactively extract existing documents.
* ``kg_extraction_runs.trigger / dispatch_state / dispatch_attempts / dispatch_error``
  — why a run exists and whether the broker actually accepted it. A run whose
  delivery failed stays in the table with its reason, so it can be re-delivered
  instead of being silently lost.

Defaults are chosen so an existing database behaves exactly as before: groups are
allowed to participate (the master switch is off by default anyway) and every
existing run is treated as already dispatched, because it was created by the manual
flow that dispatched synchronously.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_kg_auto_extract_on_import"
down_revision = "0005_kg_extraction_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_groups",
        sa.Column(
            "auto_extract",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "kg_extraction_runs",
        sa.Column(
            "trigger", sa.String(length=24), nullable=False, server_default="manual"
        ),
    )
    op.add_column(
        "kg_extraction_runs",
        sa.Column(
            "dispatch_state",
            sa.String(length=16),
            nullable=False,
            server_default="dispatched",
        ),
    )
    op.add_column(
        "kg_extraction_runs",
        sa.Column(
            "dispatch_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "kg_extraction_runs", sa.Column("dispatch_error", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_kg_extraction_runs_dispatch_state",
        "kg_extraction_runs",
        ["dispatch_state"],
    )
    # New rows created from now on start as "pending" until the broker confirms;
    # that is the model default, so only future inserts are affected.
    op.alter_column(
        "kg_extraction_runs",
        "dispatch_state",
        server_default="pending",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kg_extraction_runs_dispatch_state", table_name="kg_extraction_runs"
    )
    op.drop_column("kg_extraction_runs", "dispatch_error")
    op.drop_column("kg_extraction_runs", "dispatch_attempts")
    op.drop_column("kg_extraction_runs", "dispatch_state")
    op.drop_column("kg_extraction_runs", "trigger")
    op.drop_column("research_groups", "auto_extract")
