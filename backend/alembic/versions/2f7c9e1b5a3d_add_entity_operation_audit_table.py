"""add_entity_operation_audit_table

Revision ID: 2f7c9e1b5a3d
Revises: 1de0638c0c1b
Create Date: 2026-10-12 12:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2f7c9e1b5a3d'
down_revision: Union[str, Sequence[str], None] = '1de0638c0c1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create entity_operations_audit table for Stage 3 disambiguation, merge, and split history."""
    op.create_table(
        'entity_operations_audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operation_type', sa.String(length=50), nullable=False),
        sa.Column('primary_person_id', sa.Integer(), nullable=False),
        sa.Column('secondary_person_id', sa.Integer(), nullable=True),
        sa.Column('snapshot_details', sa.JSON(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('operator', sa.String(length=100), nullable=False),
        sa.Column('is_reverted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['primary_person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['secondary_person_id'], ['persons.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_index(
        'ix_entity_operations_audit_id',
        'entity_operations_audit',
        ['id'],
        unique=False,
    )
    op.create_index(
        'ix_entity_operations_audit_op_type',
        'entity_operations_audit',
        ['operation_type'],
        unique=False,
    )
    op.create_index(
        'ix_entity_operations_audit_primary_person',
        'entity_operations_audit',
        ['primary_person_id'],
        unique=False,
    )
    op.create_index(
        'ix_entity_operations_audit_secondary_person',
        'entity_operations_audit',
        ['secondary_person_id'],
        unique=False,
    )
    op.create_index(
        'ix_entity_operations_audit_is_reverted',
        'entity_operations_audit',
        ['is_reverted'],
        unique=False,
    )
    op.create_index(
        'ix_entity_operations_audit_created_at',
        'entity_operations_audit',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Drop entity_operations_audit table cleanly."""
    op.drop_index('ix_entity_operations_audit_created_at', table_name='entity_operations_audit')
    op.drop_index('ix_entity_operations_audit_is_reverted', table_name='entity_operations_audit')
    op.drop_index('ix_entity_operations_audit_secondary_person', table_name='entity_operations_audit')
    op.drop_index('ix_entity_operations_audit_primary_person', table_name='entity_operations_audit')
    op.drop_index('ix_entity_operations_audit_op_type', table_name='entity_operations_audit')
    op.drop_index('ix_entity_operations_audit_id', table_name='entity_operations_audit')
    op.drop_table('entity_operations_audit')
