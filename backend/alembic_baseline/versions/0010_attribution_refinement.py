"""Refine record-level attribution using each run's own execution window.

Why a second data migration
---------------------------
0008 could only attribute records on blocks that **exactly one** run had processed.
Here several runs share the same blocks (run #9 legacy mock, runs #11+ rule stub), so
thousands of rows stayed ``unknown`` even though their provenance is provable: the
record carries its own ``created_at``, and each run has ``started_at``/``finished_at``.

This revision narrows the margin of error per *record* rather than per *block*:

    attribute row R to run X  ⇔  R.block_id succeeded in X
                              ∧  R.created_at ∈ [X.started_at − 5s, X.finished_at + 5s]
                              ∧  exactly one such X exists

Anything still ambiguous (two runs' windows overlap, no ledger entry, no window) stays
NULL — i.e. ``unknown``. No provenance is invented, and ``accepted``/review state is
untouched: this migration only writes the two attribution columns.

Downgrade is intentionally a no-op. Dropping the attribution would silently destroy
provenance that is now provable, and the columns themselves are removed by 0008's
downgrade path anyway.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_attribution"
down_revision = "0009_workflow"
branch_labels = None
depends_on = None

#: Clock tolerance, identical to ``extraction_reset.WINDOW_TOLERANCE``.
TOLERANCE = "5 seconds"

_MENTION_SQL = """
WITH candidates AS (
    SELECT m.id AS record_id,
           MIN(r.id) AS run_id,
           MIN(r.source_kind) AS source_kind
    FROM person_mentions AS m
    JOIN kg_extraction_items AS i
      ON i.block_id = m.block_id
     AND i.status = 'succeeded'
    JOIN kg_extraction_runs AS r
      ON r.id = i.run_id
    WHERE m.source_kind IS NULL
      AND r.started_at IS NOT NULL
      AND m.created_at >= r.started_at - INTERVAL '{tol}'
      AND m.created_at <= COALESCE(r.finished_at, r.updated_at) + INTERVAL '{tol}'
    GROUP BY m.id
    HAVING COUNT(DISTINCT r.id) = 1
)
UPDATE person_mentions AS m
SET extraction_run_id = candidates.run_id,
    source_kind = COALESCE(candidates.source_kind, 'unknown')
FROM candidates
WHERE m.id = candidates.record_id
"""

_EVIDENCE_SQL = """
WITH candidates AS (
    SELECT e.id AS record_id,
           MIN(r.id) AS run_id,
           MIN(r.source_kind) AS source_kind
    FROM relationship_evidences AS e
    JOIN kg_extraction_items AS i
      ON i.block_id = e.block_id
     AND i.status = 'succeeded'
    JOIN kg_extraction_runs AS r
      ON r.id = i.run_id
    WHERE e.source_kind IS NULL
      AND r.started_at IS NOT NULL
      AND e.created_at >= r.started_at - INTERVAL '{tol}'
      AND e.created_at <= COALESCE(r.finished_at, r.updated_at) + INTERVAL '{tol}'
    GROUP BY e.id
    HAVING COUNT(DISTINCT r.id) = 1
)
UPDATE relationship_evidences AS e
SET extraction_run_id = candidates.run_id,
    source_kind = COALESCE(candidates.source_kind, 'unknown')
FROM candidates
WHERE e.id = candidates.record_id
"""


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(_MENTION_SQL.format(tol=TOLERANCE)))
    connection.execute(sa.text(_EVIDENCE_SQL.format(tol=TOLERANCE)))


def downgrade() -> None:
    # Data-only migration; see the module docstring. The columns are owned by 0008.
    pass
