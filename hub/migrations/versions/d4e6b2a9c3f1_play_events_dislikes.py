"""listening events, «Не нравится», Navidrome scrobble sync (MIXES.md 3.2)

Revision ID: d4e6b2a9c3f1
Revises: a7c3e9f2b1d4
Create Date: 2026-10-22 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e6b2a9c3f1'
down_revision: Union[str, None] = 'a7c3e9f2b1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('play_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('position_s', sa.Float(), nullable=True),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('context', sa.String(length=128), nullable=True),
    sa.Column('client', sa.String(length=32), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'event_id', name='uq_play_events_user_event')
    )
    with op.batch_alter_table('play_events', schema=None) as batch_op:
        batch_op.create_index('ix_play_events_user_ts', ['user_id', 'ts'], unique=False)
        batch_op.create_index('ix_play_events_song', ['nd_song_id'], unique=False)

    op.create_table('dislikes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('artist_ref', sa.String(length=64), nullable=True),
    sa.Column('artist', sa.String(length=512), nullable=True),
    sa.Column('title', sa.String(length=512), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('dislikes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dislikes_user_id'), ['user_id'], unique=False)

    op.create_table('navidrome_sync',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('last_scrobble_id', sa.Integer(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )

    # Dislikes of earlier Yandex imports (matched rows of «Не нравится»): one per user and track.
    op.execute(
        "INSERT INTO dislikes (user_id, ref, nd_song_id, artist, title, source, created_at) "
        "SELECT i.user_id, it.ref, MAX(t.nd_song_id), MAX(json_extract(it.raw_json, '$.artist')), "
        "MAX(json_extract(it.raw_json, '$.title')), 'yandex_import', MIN(i.created_at) "
        "FROM import_items it JOIN import_collections c ON c.id = it.collection_id "
        "JOIN imports i ON i.id = it.import_id LEFT JOIN tracks t ON t.id = it.track_id "
        "WHERE c.kind = 'dislikes' AND it.ref IS NOT NULL GROUP BY i.user_id, it.ref"
    )


def downgrade() -> None:
    op.drop_table('navidrome_sync')
    with op.batch_alter_table('dislikes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dislikes_user_id'))
    op.drop_table('dislikes')
    with op.batch_alter_table('play_events', schema=None) as batch_op:
        batch_op.drop_index('ix_play_events_song')
        batch_op.drop_index('ix_play_events_user_ts')
    op.drop_table('play_events')
