"""Person role provenance: raw role wording, where it came from, and per-mention role.

Why these three columns
-----------------------
The extraction prompt asks every entity for a ``role`` and the model returns one, but
the value was dropped on write: ``person_mentions`` had no role column and the person
factory was called with the name only. Every one of the 367 persons therefore had
``primary_role IS NULL`` and the graph could only ever paint one colour.

Keeping provenance next to the wording matters because a role is a claim:

* ``persons.role_source`` — model_extracted / human_verified / heuristic / unknown;
  NULL means "no role recorded", which is *not* the same as "other".
* ``person_mentions.role_text`` — the wording as reported for that single occurrence,
  tied to its block, run and source kind, so the sentence behind a role is findable.
* ``person_mentions.role_source`` — provenance of that occurrence's wording.

No data is invented here: existing rows keep ``NULL`` roles and ``NULL`` sources, and
nothing is back-filled. Downgrade drops only these three columns.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_person_roles"
down_revision = "0010_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("persons", sa.Column("role_source", sa.String(length=24), nullable=True))
    op.create_index("ix_persons_role_source", "persons", ["role_source"], unique=False)

    op.add_column(
        "person_mentions", sa.Column("role_text", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "person_mentions", sa.Column("role_source", sa.String(length=24), nullable=True)
    )
    op.create_index(
        "ix_person_mentions_role_source", "person_mentions", ["role_source"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_person_mentions_role_source", table_name="person_mentions")
    op.drop_column("person_mentions", "role_source")
    op.drop_column("person_mentions", "role_text")
    op.drop_index("ix_persons_role_source", table_name="persons")
    op.drop_column("persons", "role_source")
