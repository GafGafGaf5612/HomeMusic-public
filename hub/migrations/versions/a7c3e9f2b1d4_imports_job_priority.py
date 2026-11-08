"""imports from music services, shared source map, job priority

Revision ID: a7c3e9f2b1d4
Revises: 8f2d4c6a1e93
Create Date: 2026-10-21 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e9f2b1d4'
down_revision: Union[str, None] = '8f2d4c6a1e93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('priority', sa.Integer(), nullable=False, server_default='0'))

    op.create_table('imports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('account', sa.String(length=256), nullable=True),
    sa.Column('token_enc', sa.Text(), nullable=True),
    sa.Column('keep_token', sa.Boolean(), nullable=False),
    sa.Column('auth_json', sa.JSON(), nullable=False),
    sa.Column('options_json', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('imports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_imports_user_id'), ['user_id'], unique=False)

    op.create_table('import_collections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('selected', sa.Boolean(), nullable=False),
    sa.Column('cover_url', sa.String(length=1024), nullable=True),
    sa.Column('nd_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('nd_song_ids', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_collections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_collections_import_id'), ['import_id'], unique=False)

    op.create_table('import_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.Column('collection_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('raw_json', sa.JSON(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('match', sa.String(length=16), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('download_item_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['collection_id'], ['import_collections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['download_item_id'], ['download_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_items_import_id'), ['import_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_items_collection_id'), ['collection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_items_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_items_download_item_id'), ['download_item_id'], unique=False)
        batch_op.create_index('ix_import_items_ref', ['ref'], unique=False)

    op.create_table('source_map',
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('match', sa.String(length=16), nullable=False),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('source', 'kind', 'external_id')
    )


def downgrade() -> None:
    op.drop_table('source_map')
    op.drop_table('import_items')
    op.drop_table('import_collections')
    op.drop_table('imports')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('priority')
