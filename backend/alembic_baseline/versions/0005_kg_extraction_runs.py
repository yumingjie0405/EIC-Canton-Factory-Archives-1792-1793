"""Create the KG extraction ledger tables.

Why a migration and not ``create_all``: the ledger is the audit trail of research
work (which block was extracted, how many tokens, why a run paused). It must arrive
through a reviewed migration like ``research_sessions`` and the group tables.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_kg_extraction_runs"
down_revision = "0004_document_type_docx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kg_extraction_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("research_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("total_blocks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_blocks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_blocks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_blocks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_blocks", sa.Integer(), nullable=True),
        sa.Column("budget_tokens", sa.Integer(), nullable=True),
        sa.Column("max_consecutive_failures", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cursor_block_id", sa.Integer(), nullable=True),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_kg_extraction_runs_scope_id", "kg_extraction_runs", ["scope_id"])
    op.create_index("ix_kg_extraction_runs_group_id", "kg_extraction_runs", ["group_id"])
    op.create_index("ix_kg_extraction_runs_document_id", "kg_extraction_runs", ["document_id"])
    op.create_index("ix_kg_extraction_runs_status", "kg_extraction_runs", ["status"])
    op.create_index(
        "ix_kg_extraction_runs_scope", "kg_extraction_runs", ["scope_type", "scope_id"]
    )

    op.create_table(
        "kg_extraction_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("kg_extraction_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "block_id",
            sa.Integer(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "block_id", name="uq_kg_extraction_items_run_block"),
    )
    op.create_index("ix_kg_extraction_items_run_id", "kg_extraction_items", ["run_id"])
    op.create_index("ix_kg_extraction_items_block_id", "kg_extraction_items", ["block_id"])


def downgrade() -> None:
    op.drop_index("ix_kg_extraction_items_block_id", table_name="kg_extraction_items")
    op.drop_index("ix_kg_extraction_items_run_id", table_name="kg_extraction_items")
    op.drop_table("kg_extraction_items")
    op.drop_index("ix_kg_extraction_runs_scope", table_name="kg_extraction_runs")
    op.drop_index("ix_kg_extraction_runs_status", table_name="kg_extraction_runs")
    op.drop_index("ix_kg_extraction_runs_document_id", table_name="kg_extraction_runs")
    op.drop_index("ix_kg_extraction_runs_group_id", table_name="kg_extraction_runs")
    op.drop_index("ix_kg_extraction_runs_scope_id", table_name="kg_extraction_runs")
    op.drop_table("kg_extraction_runs")
