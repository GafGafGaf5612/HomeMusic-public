"""Playback attempts and cumulative measured listening time; preserve all existing rows."""
from alembic import op
import sqlalchemy as sa

revision = "b61d9a7e4c20"
down_revision = "a4c9e1027d35"
branch_labels = depends_on = None


def upgrade():
    op.add_column("play_events", sa.Column("playback_id", sa.String(64)))
    op.add_column("play_events", sa.Column("device_id", sa.String(64)))
    op.add_column("play_events", sa.Column("listened_ms", sa.Integer()))
    op.add_column("play_events", sa.Column("track_metadata", sa.JSON()))
    op.add_column("play_events", sa.Column("superseded_scrobble", sa.Boolean(), nullable=False, server_default="0"))
    op.create_index("ix_play_events_playback", "play_events", ["user_id", "device_id", "playback_id"])


def downgrade():
    op.drop_index("ix_play_events_playback", "play_events")
    for name in ("superseded_scrobble", "track_metadata", "listened_ms", "device_id", "playback_id"):
        op.drop_column("play_events", name)
