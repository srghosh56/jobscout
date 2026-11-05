"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-02 00:14:37.146706
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import pgvector.sqlalchemy

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('api_keys',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('is_demo', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('prefix')
    )
    op.create_table('embedding_cache',
    sa.Column('text_hash', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('text_hash', 'model')
    )
    op.create_table('ingest_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=16), server_default='ingest', nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('fetched', sa.Integer(), server_default='0', nullable=False),
    sa.Column('inserted', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated', sa.Integer(), server_default='0', nullable=False),
    sa.Column('unchanged', sa.Integer(), server_default='0', nullable=False),
    sa.Column('duplicates', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('llm_cache',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tokens_in', sa.Integer(), server_default='0', nullable=False),
    sa.Column('tokens_out', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('postings',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('source_id', sa.String(length=256), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('company', sa.Text(), nullable=True),
    sa.Column('raw_text', sa.Text(), server_default='', nullable=False),
    sa.Column('posting_language', sa.String(length=2), server_default='en', nullable=False),
    sa.Column('city', sa.Text(), nullable=True),
    sa.Column('cities', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('country', sa.Text(), nullable=True),
    sa.Column('locations', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('job_family', sa.String(length=64), nullable=True),
    sa.Column('source_remote_flag', sa.Boolean(), nullable=True),
    sa.Column('remote_policy', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('seniority', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('min_years_experience', sa.SmallInteger(), nullable=True),
    sa.Column('required_skills', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('nice_to_have_skills', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('languages_required', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('german_level', sa.String(length=16), server_default='none', nullable=False),
    sa.Column('english_sufficient', sa.String(length=8), server_default='unclear', nullable=False),
    sa.Column('contract_type', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('contract_duration_months', sa.SmallInteger(), nullable=True),
    sa.Column('salary_min', sa.Float(), nullable=True),
    sa.Column('salary_max', sa.Float(), nullable=True),
    sa.Column('salary_currency', sa.String(length=3), nullable=True),
    sa.Column('salary_period', sa.String(length=8), nullable=True),
    sa.Column('annual_salary_min_eur', sa.Float(), nullable=True),
    sa.Column('annual_salary_max_eur', sa.Float(), nullable=True),
    sa.Column('salary_stated', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('bluecard_status', sa.String(length=24), server_default='salary_not_stated', nullable=False),
    sa.Column('visa_sponsorship', sa.String(length=16), server_default='not_mentioned', nullable=False),
    sa.Column('relocation_support', sa.String(length=16), server_default='not_mentioned', nullable=False),
    sa.Column('application_deadline', sa.Date(), nullable=True),
    sa.Column('published_at', sa.Date(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('consecutive_failures', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('inactive_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('duplicate_of', sa.String(length=32), nullable=True),
    sa.Column('extraction_status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('extraction_error', sa.Text(), nullable=True),
    sa.Column('model_version', sa.String(length=128), nullable=True),
    sa.Column('schema_version', sa.String(length=32), nullable=True),
    sa.Column('prompt_version', sa.String(length=32), nullable=True),
    sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tsv_de', postgresql.TSVECTOR(), sa.Computed("CASE WHEN posting_language = 'de' THEN setweight(to_tsvector('german'::regconfig, coalesce(title, '')), 'A') || setweight(to_tsvector('german'::regconfig, coalesce(raw_text, '')), 'B') END", persisted=True), nullable=True),
    sa.Column('tsv_en', postgresql.TSVECTOR(), sa.Computed("CASE WHEN posting_language = 'en' THEN setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || setweight(to_tsvector('english'::regconfig, coalesce(raw_text, '')), 'B') END", persisted=True), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('embedding_model', sa.String(length=128), nullable=True),
    sa.Column('embedded_hash', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source', 'source_id', name='uq_postings_source_source_id')
    )
    op.create_index('ix_postings_city_active', 'postings', ['city', 'is_active'], unique=False)
    op.create_index('ix_postings_content_hash', 'postings', ['content_hash'], unique=False)
    op.create_index('ix_postings_embedding_hnsw', 'postings', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('ix_postings_required_skills', 'postings', ['required_skills'], unique=False, postgresql_using='gin')
    op.create_index('ix_postings_tsv_de', 'postings', ['tsv_de'], unique=False, postgresql_using='gin')
    op.create_index('ix_postings_tsv_en', 'postings', ['tsv_en'], unique=False, postgresql_using='gin')
    op.create_table('cv_profiles',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('profile', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('api_key_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('feedback',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('trace_id', sa.String(length=64), nullable=True),
    sa.Column('session_id', sa.String(length=64), nullable=True),
    sa.Column('message_id', sa.String(length=64), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('review_status', sa.String(length=16), server_default='new', nullable=False),
    sa.Column('api_key_id', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('saved_searches',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('query', sa.Text(), nullable=True),
    sa.Column('filters', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('api_key_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('saved_searches')
    op.drop_table('feedback')
    op.drop_table('cv_profiles')
    op.drop_index('ix_postings_tsv_en', table_name='postings', postgresql_using='gin')
    op.drop_index('ix_postings_tsv_de', table_name='postings', postgresql_using='gin')
    op.drop_index('ix_postings_required_skills', table_name='postings', postgresql_using='gin')
    op.drop_index('ix_postings_embedding_hnsw', table_name='postings', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_postings_content_hash', table_name='postings')
    op.drop_index('ix_postings_city_active', table_name='postings')
    op.drop_table('postings')
    op.drop_table('llm_cache')
    op.drop_table('ingest_runs')
    op.drop_table('embedding_cache')
    op.drop_table('api_keys')
