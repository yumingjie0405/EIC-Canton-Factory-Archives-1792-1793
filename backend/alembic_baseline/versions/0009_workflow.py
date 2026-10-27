"""Workflow tables: review audit, reproducible graph snapshots, block versions.

Why these exist
---------------
The review workflow needs to answer "who changed what, when, and can it be undone",
which ``entity_operations_audit`` cannot do for relationship reviews or mention
batches (it requires a person on both sides). A snapshot also has to store the nodes
and edges that were actually counted — a timestamp alone is not a reproducible
snapshot, and current data must never be presented as a past state.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_workflow"
down_revision = "0008_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kg_review_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("research_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "relationship_id",
            sa.Integer(),
            sa.ForeignKey("relationships.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "person_id",
            sa.Integer(),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mention_id",
            sa.Integer(),
            sa.ForeignKey("person_mentions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("affected_counts", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(length=100), nullable=False, server_default="system"),
        sa.Column("is_reverted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("operation_type", "group_id", "relationship_id", "person_id", "batch_id", "is_reverted", "created_at"):
        op.create_index("ix_kg_review_audit_%s" % column, "kg_review_audit", [column])

    op.create_table(
        "kg_graph_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("research_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("group_name", sa.String(length=200), nullable=True),
        sa.Column("view", sa.String(length=20), nullable=False, server_default="all"),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("algorithm_version", sa.String(length=100), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("node_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edge_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("persons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("relationships", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metrics_summary", sa.JSON(), nullable=True),
        sa.Column("excluded", sa.JSON(), nullable=True),
        sa.Column("data_trust", sa.JSON(), nullable=True),
        sa.Column("extraction_run_ids", sa.JSON(), nullable=True),
        sa.Column("capability", sa.String(length=24), nullable=False, server_default="described"),
        sa.Column("created_by", sa.String(length=100), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kg_graph_snapshots_run_id", "kg_graph_snapshots", ["run_id"])
    op.create_index("ix_kg_graph_snapshots_group_id", "kg_graph_snapshots", ["group_id"])
    op.create_index("ix_kg_graph_snapshots_created_at", "kg_graph_snapshots", ["created_at"])
    op.create_index(
        "ix_kg_graph_snapshots_group_created", "kg_graph_snapshots", ["group_id", "created_at"]
    )

    op.create_table(
        "kg_block_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "block_id",
            sa.Integer(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("text_excerpt", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="import"),
        sa.Column(
            "supersedes_id",
            sa.Integer(),
            sa.ForeignKey("kg_block_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_uncertain", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kg_block_versions_block_id", "kg_block_versions", ["block_id"])
    op.create_index("ix_kg_block_versions_content_hash", "kg_block_versions", ["content_hash"])


def downgrade() -> None:
    for index in (
        "ix_kg_block_versions_content_hash",
        "ix_kg_block_versions_block_id",
    ):
        op.drop_index(index, table_name="kg_block_versions")
    op.drop_table("kg_block_versions")

    for index in (
        "ix_kg_graph_snapshots_group_created",
        "ix_kg_graph_snapshots_created_at",
        "ix_kg_graph_snapshots_group_id",
        "ix_kg_graph_snapshots_run_id",
    ):
        op.drop_index(index, table_name="kg_graph_snapshots")
    op.drop_table("kg_graph_snapshots")

    for column in (
        "created_at",
        "is_reverted",
        "batch_id",
        "person_id",
        "relationship_id",
        "group_id",
        "operation_type",
    ):
        op.drop_index("ix_kg_review_audit_%s" % column, table_name="kg_review_audit")
    op.drop_table("kg_review_audit")
