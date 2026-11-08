"""«Микс дня»: mixes, their songs, users' mix settings (MIXES.md 6)

Revision ID: e7a1c5d93b20
Revises: d4e6b2a9c3f1
Create Date: 2026-10-22 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7a1c5d93b20'
down_revision: Union[str, None] = 'd4e6b2a9c3f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('mix_settings',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('mix_hour', sa.Integer(), nullable=False),
    sa.Column('nd_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('artists_json', sa.JSON(), nullable=False),
    sa.Column('last_build_on', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('mixes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('nd_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('stats_json', sa.JSON(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['batch_id'], ['download_batches.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mixes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mixes_user_id'), ['user_id'], unique=False)

    op.create_table('mix_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mix_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('familiar', sa.Boolean(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('generator', sa.String(length=32), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('features_json', sa.JSON(), nullable=False),
    sa.Column('downloaded_for_mix', sa.Boolean(), nullable=False),
    sa.Column('download_item_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['mix_id'], ['mixes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['download_item_id'], ['download_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mix_items', schema=None) as batch_op:
        batch_op.create_index('ix_mix_items_mix', ['mix_id', 'state', 'position'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_ref'), ['ref'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_nd_song_id'), ['nd_song_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_track_id'), ['track_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_download_item_id'), ['download_item_id'], unique=False)


def downgrade() -> None:
    op.drop_table('mix_items')
    op.drop_table('mixes')
    op.drop_table('mix_settings')
