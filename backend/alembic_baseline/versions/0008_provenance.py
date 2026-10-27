"""Provenance columns: where each KG record actually came from.

Why this migration exists
-------------------------
Run #9 wrote 14,620 relationship evidences with a fixed mock output and run #11+ wrote
rule-derived rows from the archive text. Both look identical in the tables: nothing
recorded which producer wrote a mention or an evidence row, and ``tokens_used`` gave no
hint whether it was supplier usage or a stub counter. Without that, a reviewed record
could be presented as an archival finding when it is really test output.

This revision adds exactly the missing provenance:

* ``kg_extraction_runs.source_kind`` — provider_reported / mock_constant /
  mock_text_derived / historical_seed / unknown.
* ``kg_extraction_runs.token_source`` — provider / estimated / unknown.
* ``person_mentions.extraction_run_id`` + ``source_kind``.
* ``relationship_evidences.extraction_run_id`` + ``source_kind``.

Backfill policy (deliberately conservative)
-------------------------------------------
* Runs get ``source_kind`` from their ``model`` string, and ``token_source`` from that
  kind (mock -> ``estimated``, otherwise ``provider`` only when tokens were recorded).
* Mentions/evidences are attributed **only** when the ledger proves it: their block
  succeeded in exactly one run *and* the row was written inside that run's window.
  Everything else keeps NULL, which every read path must render as ``unknown``. No
  record is ever assigned a provenance by guesswork.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_provenance"
down_revision = "0007_extraction_safety"
branch_labels = None
depends_on = None

#: Model-name fragments treated as rule-derived stubs (mirrors ``KG_TEXT_DERIVED_MODEL_NAMES``).
TEXT_DERIVED_MARKERS = ("text-stub", "text_stub", "textderived", "text-derived")
#: Model names treated as the old constant mock (mirrors ``KG_MOCK_MODEL_NAMES``).
MOCK_MARKERS = ("mock", "stub", "fake", "dummy")


def _classify(model):
    if not model:
        return "unknown"
    lowered = str(model).strip().lower()
    if any(marker in lowered for marker in TEXT_DERIVED_MARKERS):
        return "mock_text_derived"
    if any(marker in lowered for marker in MOCK_MARKERS):
        return "mock_constant"
    return "provider_reported"


def upgrade() -> None:
    op.add_column(
        "kg_extraction_runs", sa.Column("source_kind", sa.String(length=24), nullable=True)
    )
    op.add_column(
        "kg_extraction_runs", sa.Column("token_source", sa.String(length=16), nullable=True)
    )
    op.create_index(
        "ix_kg_extraction_runs_source_kind", "kg_extraction_runs", ["source_kind"]
    )

    op.add_column("person_mentions", sa.Column("extraction_run_id", sa.Integer(), nullable=True))
    op.add_column("person_mentions", sa.Column("source_kind", sa.String(length=24), nullable=True))
    op.create_foreign_key(
        "fk_person_mentions_extraction_run_id",
        "person_mentions",
        "kg_extraction_runs",
        ["extraction_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_person_mentions_extraction_run_id", "person_mentions", ["extraction_run_id"]
    )
    op.create_index("ix_person_mentions_source_kind", "person_mentions", ["source_kind"])

    op.add_column(
        "relationship_evidences", sa.Column("extraction_run_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "relationship_evidences", sa.Column("source_kind", sa.String(length=24), nullable=True)
    )
    op.create_foreign_key(
        "fk_relationship_evidences_extraction_run_id",
        "relationship_evidences",
        "kg_extraction_runs",
        ["extraction_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_relationship_evidences_extraction_run_id",
        "relationship_evidences",
        ["extraction_run_id"],
    )
    op.create_index(
        "ix_relationship_evidences_source_kind", "relationship_evidences", ["source_kind"]
    )

    connection = op.get_bind()

    # ------------------------------------------------------------------ runs
    rows = connection.execute(
        sa.text("SELECT id, model, tokens_used FROM kg_extraction_runs")
    ).fetchall()
    for run_id, model, tokens_used in rows:
        kind = _classify(model)
        if kind == "unknown":
            token_source = "unknown"
        elif kind == "provider_reported":
            token_source = "provider" if (tokens_used or 0) > 0 else "unknown"
        else:
            token_source = "estimated" if (tokens_used or 0) > 0 else "unknown"
        connection.execute(
            sa.text(
                "UPDATE kg_extraction_runs SET source_kind = :kind, token_source = :tokens "
                "WHERE id = :run_id"
            ),
            {"kind": kind, "tokens": token_source, "run_id": run_id},
        )

    # -------------------------------------------- mentions / evidences (provable only)
    # A block is attributable when exactly one run reports it as succeeded. Records on
    # such a block that were also written inside that run's execution window inherit the
    # run's provenance; everything else stays NULL (= unknown).
    unique_run_per_block = sa.text(
        """
        SELECT item.block_id AS block_id,
               MIN(item.run_id) AS run_id,
               MIN(run.started_at) AS started_at,
               MIN(COALESCE(run.finished_at, run.updated_at)) AS finished_at,
               MIN(run.source_kind) AS source_kind
        FROM kg_extraction_items AS item
        JOIN kg_extraction_runs AS run ON run.id = item.run_id
        WHERE item.status = 'succeeded'
        GROUP BY item.block_id
        HAVING COUNT(DISTINCT item.run_id) = 1
        """
    )
    connection.execute(
        sa.text(
            """
            UPDATE person_mentions AS m
            SET extraction_run_id = prov.run_id,
                source_kind = prov.source_kind
            FROM ({provable}) AS prov
            WHERE m.block_id = prov.block_id
              AND m.extraction_run_id IS NULL
              AND m.created_at >= prov.started_at - INTERVAL '5 seconds'
              AND m.created_at <= COALESCE(prov.finished_at, prov.started_at) + INTERVAL '5 seconds'
            """.format(provable=unique_run_per_block.text)
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE relationship_evidences AS e
            SET extraction_run_id = prov.run_id,
                source_kind = prov.source_kind
            FROM ({provable}) AS prov
            WHERE e.block_id = prov.block_id
              AND e.extraction_run_id IS NULL
              AND e.created_at >= prov.started_at - INTERVAL '5 seconds'
              AND e.created_at <= COALESCE(prov.finished_at, prov.started_at) + INTERVAL '5 seconds'
            """.format(provable=unique_run_per_block.text)
        )
    )


def downgrade() -> None:
    op.drop_index("ix_relationship_evidences_source_kind", table_name="relationship_evidences")
    op.drop_index(
        "ix_relationship_evidences_extraction_run_id", table_name="relationship_evidences"
    )
    op.drop_constraint(
        "fk_relationship_evidences_extraction_run_id",
        "relationship_evidences",
        type_="foreignkey",
    )
    op.drop_column("relationship_evidences", "source_kind")
    op.drop_column("relationship_evidences", "extraction_run_id")

    op.drop_index("ix_person_mentions_source_kind", table_name="person_mentions")
    op.drop_index("ix_person_mentions_extraction_run_id", table_name="person_mentions")
    op.drop_constraint(
        "fk_person_mentions_extraction_run_id", "person_mentions", type_="foreignkey"
    )
    op.drop_column("person_mentions", "source_kind")
    op.drop_column("person_mentions", "extraction_run_id")

    op.drop_index("ix_kg_extraction_runs_source_kind", table_name="kg_extraction_runs")
    op.drop_column("kg_extraction_runs", "token_source")
    op.drop_column("kg_extraction_runs", "source_kind")
