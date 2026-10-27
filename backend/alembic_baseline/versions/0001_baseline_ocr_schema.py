"""Baseline: the complete pre-existing schema of the product database.

Provenance
----------
This revision replays ``schema/baseline_ocr_platform_20261022.sql``, which is a
byte-faithful copy of the verified ``pg_dump --schema-only`` snapshot
``ocr_platform_pre_r1042_20261022_183504.schema.sql``
(SHA-256 ``5616E5481C555BF5DB5B3F91889BA6186060C27BA802988408B64B4B88F2901``,
36,191 bytes, PostgreSQL 15.19). The only transformation is the removal of two
psql meta-commands (``\\restrict`` / ``\\unrestrict``), which are not SQL and
cannot be sent through a DBAPI.

Why replay the audited DDL instead of autogenerating from the ORM
----------------------------------------------------------------
The snapshot is the authoritative record of the live schema (12 tables,
155 columns, 7 enum types, 31 enum labels, 37 constraints, 67 indexes,
12 sequences, ownership). It was cross-checked against the R1.0.4 read-only
catalog capture and against the ORM metadata before this revision was written;
the comparison report is
``eic_preflight_artifacts/S8_R2_1_pg_integration/baseline_consistency.json``.
Autogenerating from the ORM would have produced a "plausible" baseline that
silently omits whatever the ORM does not model; replaying audited DDL cannot.

Downgrade
---------
Deliberately unimplemented: dropping the archival tables would destroy the
only copy of the research corpus. A rollback of this chain must be a reviewed
restore from a verified backup, never an automatically generated DROP.

Revision id length
------------------
Alembic creates ``version_num`` as ``VARCHAR(32)``, so the id must stay within
32 characters (found the hard way: a 33-character id failed with
``StringDataRightTruncation`` on the first real upgrade attempt).
"""
import io
import os

from alembic import op

revision = "0001_baseline_ocr_schema"
down_revision = None  # this is the single root of the baseline chain
branch_labels = None
depends_on = None

_SQL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "schema",
    "baseline_ocr_platform_20261022.sql",
)


def _baseline_sql() -> str:
    with io.open(_SQL_PATH, encoding="utf-8") as handle:
        return handle.read()


def upgrade() -> None:
    op.execute(_baseline_sql())
    # The dump starts with ``set_config('search_path', '', false)`` (pg_dump
    # emits fully-qualified names). That session setting would also apply to
    # Alembic's own ``INSERT INTO alembic_version_baseline``, so restore a
    # normal search_path before this revision finishes.
    op.execute("SELECT pg_catalog.set_config('search_path', 'public', false)")


def downgrade() -> None:
    raise RuntimeError(
        "downgrade is not supported for the full-schema baseline chain: it would "
        "drop the archival tables. Restore from a verified backup instead "
        "(see docs/implementation/stage8_phaseA_R1_0_4_6_downgrade_safety.md)."
    )
