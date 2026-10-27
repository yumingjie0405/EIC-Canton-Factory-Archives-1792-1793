"""add_knowledge_graph_tables

Revision ID: 1de0638c0c1b
Revises: 
Create Date: 2026-10-09 12:19:08.056482

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1de0638c0c1b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Stage 1 Knowledge Graph tables without affecting existing tables."""
    # 1. persons table
    op.create_table(
        'persons',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('canonical_name_en', sa.String(length=255), nullable=True),
        sa.Column('canonical_name_zh', sa.String(length=255), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('aliases', sa.JSON(), nullable=True),
        sa.Column('primary_role', sa.String(length=100), nullable=True),
        sa.Column('organization', sa.String(length=255), nullable=True),
        sa.Column('birth_year', sa.SmallInteger(), nullable=True),
        sa.Column('death_year', sa.SmallInteger(), nullable=True),
        sa.Column('active_start', sa.Date(), nullable=True),
        sa.Column('active_end', sa.Date(), nullable=True),
        sa.Column('base_location', sa.String(length=255), nullable=True),
        sa.Column('biographical_notes', sa.Text(), nullable=True),
        sa.Column('identity_status', sa.String(length=30), nullable=False),
        sa.Column('review_status', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('birth_year IS NULL OR death_year IS NULL OR birth_year <= death_year', name='chk_person_birth_death'),
        sa.CheckConstraint('active_start IS NULL OR active_end IS NULL OR active_start <= active_end', name='chk_person_active_dates'),
        sa.CheckConstraint('length(trim(display_name)) > 0', name='chk_person_display_name_nonempty'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_persons_id'), 'persons', ['id'], unique=False)
    op.create_index(op.f('ix_persons_display_name'), 'persons', ['display_name'], unique=False)
    op.create_index(op.f('ix_persons_canonical_name_en'), 'persons', ['canonical_name_en'], unique=False)
    op.create_index(op.f('ix_persons_canonical_name_zh'), 'persons', ['canonical_name_zh'], unique=False)
    op.create_index(op.f('ix_persons_primary_role'), 'persons', ['primary_role'], unique=False)
    op.create_index(op.f('ix_persons_identity_status'), 'persons', ['identity_status'], unique=False)
    op.create_index(op.f('ix_persons_review_status'), 'persons', ['review_status'], unique=False)

    # 2. person_mentions table
    op.create_table(
        'person_mentions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('block_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('normalized_text', sa.Text(), nullable=True),
        sa.Column('mention_type', sa.String(length=50), nullable=False),
        sa.Column('temporal_date', sa.Date(), nullable=True),
        sa.Column('temporal_year', sa.SmallInteger(), nullable=True),
        sa.Column('spatial_location', sa.String(length=255), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('resolution_status', sa.String(length=30), nullable=False),
        sa.Column('review_status', sa.String(length=30), nullable=False),
        sa.Column('candidate_person_ids', sa.JSON(), nullable=True),
        sa.Column('evidence_text', sa.Text(), nullable=True),
        sa.Column('start_offset', sa.Integer(), nullable=True),
        sa.Column('end_offset', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)', name='chk_mention_confidence_range'),
        sa.CheckConstraint('length(trim(raw_text)) > 0', name='chk_mention_raw_text_nonempty'),
        sa.ForeignKeyConstraint(['block_id'], ['blocks.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_person_mentions_id'), 'person_mentions', ['id'], unique=False)
    op.create_index(op.f('ix_person_mentions_block_id'), 'person_mentions', ['block_id'], unique=False)
    op.create_index(op.f('ix_person_mentions_person_id'), 'person_mentions', ['person_id'], unique=False)
    op.create_index(op.f('ix_person_mentions_normalized_text'), 'person_mentions', ['normalized_text'], unique=False)
    op.create_index(op.f('ix_person_mentions_temporal_date'), 'person_mentions', ['temporal_date'], unique=False)
    op.create_index(op.f('ix_person_mentions_resolution_status'), 'person_mentions', ['resolution_status'], unique=False)
    op.create_index(op.f('ix_person_mentions_review_status'), 'person_mentions', ['review_status'], unique=False)
    op.create_index('ix_person_mentions_status', 'person_mentions', ['resolution_status', 'review_status'], unique=False)

    # 3. relationships table
    op.create_table(
        'relationships',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_person_id', sa.Integer(), nullable=False),
        sa.Column('target_person_id', sa.Integer(), nullable=False),
        sa.Column('relation_type', sa.String(length=80), nullable=False),
        sa.Column('relation_label', sa.String(length=255), nullable=True),
        sa.Column('directionality', sa.String(length=20), nullable=False),
        sa.Column('weight', sa.Float(), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('time_description', sa.String(length=255), nullable=True),
        sa.Column('spatial_location', sa.String(length=255), nullable=True),
        sa.Column('evidence_summary', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('review_status', sa.String(length=30), nullable=False),
        sa.Column('claim_status', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to', name='chk_relationship_dates'),
        sa.CheckConstraint('confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)', name='chk_relationship_confidence_range'),
        sa.CheckConstraint('weight IS NULL OR weight >= 0.0', name='chk_relationship_weight_positive'),
        sa.ForeignKeyConstraint(['source_person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_relationships_id'), 'relationships', ['id'], unique=False)
    op.create_index(op.f('ix_relationships_source_person_id'), 'relationships', ['source_person_id'], unique=False)
    op.create_index(op.f('ix_relationships_target_person_id'), 'relationships', ['target_person_id'], unique=False)
    op.create_index(op.f('ix_relationships_relation_type'), 'relationships', ['relation_type'], unique=False)
    op.create_index('ix_relationships_temporal', 'relationships', ['valid_from', 'valid_to'], unique=False)

    # 4. relationship_evidences table
    op.create_table(
        'relationship_evidences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('relationship_id', sa.Integer(), nullable=False),
        sa.Column('person_mention_id', sa.Integer(), nullable=True),
        sa.Column('block_id', sa.Integer(), nullable=False),
        sa.Column('evidence_role', sa.String(length=30), nullable=False),
        sa.Column('quote_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['block_id'], ['blocks.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['person_mention_id'], ['person_mentions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['relationship_id'], ['relationships.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_relationship_evidences_id'), 'relationship_evidences', ['id'], unique=False)
    op.create_index(op.f('ix_relationship_evidences_relationship_id'), 'relationship_evidences', ['relationship_id'], unique=False)
    op.create_index(op.f('ix_relationship_evidences_person_mention_id'), 'relationship_evidences', ['person_mention_id'], unique=False)
    op.create_index(op.f('ix_relationship_evidences_block_id'), 'relationship_evidences', ['block_id'], unique=False)

    # 5. external_authorities table
    op.create_table(
        'external_authorities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('authority_source', sa.String(length=100), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('external_url', sa.Text(), nullable=True),
        sa.Column('record_title', sa.String(length=500), nullable=True),
        sa.Column('historical_notes', sa.Text(), nullable=True),
        sa.Column('match_confidence', sa.Float(), nullable=True),
        sa.Column('review_status', sa.String(length=30), nullable=False),
        sa.Column('claim_status', sa.String(length=30), nullable=False),
        sa.Column('accessed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('match_confidence IS NULL OR (match_confidence >= 0.0 AND match_confidence <= 1.0)', name='chk_authority_confidence_range'),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_external_authorities_id'), 'external_authorities', ['id'], unique=False)
    op.create_index(op.f('ix_external_authorities_person_id'), 'external_authorities', ['person_id'], unique=False)
    op.create_index(op.f('ix_external_authorities_authority_source'), 'external_authorities', ['authority_source'], unique=False)
    op.create_index(op.f('ix_external_authorities_external_id'), 'external_authorities', ['external_id'], unique=False)
    op.create_index('ix_external_authorities_source_id', 'external_authorities', ['authority_source', 'external_id'], unique=False)

    # 6. sna_graph_metrics table
    op.create_table(
        'sna_graph_metrics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=64), nullable=False),
        sa.Column('snapshot_at', sa.DateTime(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=True),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('graph_scope', sa.String(length=100), nullable=False),
        sa.Column('degree_centrality', sa.Float(), nullable=True),
        sa.Column('betweenness_centrality', sa.Float(), nullable=True),
        sa.Column('closeness_centrality', sa.Float(), nullable=True),
        sa.Column('pagerank', sa.Float(), nullable=True),
        sa.Column('community_id', sa.String(length=100), nullable=True),
        sa.Column('algorithm_version', sa.String(length=100), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['person_id'], ['persons.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'person_id', name='uq_sna_run_person')
    )
    op.create_index(op.f('ix_sna_graph_metrics_id'), 'sna_graph_metrics', ['id'], unique=False)
    op.create_index(op.f('ix_sna_graph_metrics_person_id'), 'sna_graph_metrics', ['person_id'], unique=False)
    op.create_index(op.f('ix_sna_graph_metrics_run_id'), 'sna_graph_metrics', ['run_id'], unique=False)
    op.create_index(op.f('ix_sna_graph_metrics_community_id'), 'sna_graph_metrics', ['community_id'], unique=False)
    op.create_index('ix_sna_metrics_run_scope', 'sna_graph_metrics', ['run_id', 'graph_scope'], unique=False)


def downgrade() -> None:
    """Drop Stage 1 Knowledge Graph tables cleanly in reverse dependency order."""
    op.drop_table('sna_graph_metrics')
    op.drop_table('external_authorities')
    op.drop_table('relationship_evidences')
    op.drop_table('relationships')
    op.drop_table('person_mentions')
    op.drop_table('persons')
