"""Typed favorites ledger and verified subject aliases (no network or data deletion)."""
from alembic import op
import sqlalchemy as sa

revision = "a4c9e1027d35"
down_revision = "f2b7c4e81a06"
branch_labels = depends_on = None


def upgrade():
    op.add_column("play_events", sa.Column("target_type", sa.String(16), nullable=False, server_default="track"))
    for name in ("nd_album_id", "nd_artist_id", "reverts_event_id"):
        op.add_column("play_events", sa.Column(name, sa.String(64)))
    op.add_column("play_events", sa.Column("target_data", sa.JSON()))
    op.add_column("play_events", sa.Column("mirror_pending", sa.Boolean(), nullable=False, server_default="0"))
    op.create_index("ix_play_events_user_target_ts", "play_events", ["user_id", "target_type", "ts", "id"])
    for name in ("nd_album_id", "nd_artist_id"):
        op.create_index("ix_play_events_" + name, "play_events", [name])
    op.create_table("favorite_subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("canonical_key", sa.String(128), nullable=False),
        sa.Column("ref", sa.String(64)), sa.Column("nd_id", sa.String(64)),
        sa.Column("aliases", sa.JSON(), nullable=False), sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("enriched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("target_type", "canonical_key", name="uq_favorite_subject_key"))
    for name in ("ref", "nd_id"):
        op.create_index("ix_favorite_subjects_" + name, "favorite_subjects", [name])
    for name in ("favorites_synced_at", "favorites_bootstrapped_at"):
        op.add_column("navidrome_sync", sa.Column(name, sa.DateTime()))


def downgrade():
    for name in ("favorites_synced_at", "favorites_bootstrapped_at"):
        op.drop_column("navidrome_sync", name)
    op.drop_table("favorite_subjects")
    for name in ("nd_album_id", "nd_artist_id"):
        op.drop_index("ix_play_events_" + name, "play_events")
    op.drop_index("ix_play_events_user_target_ts", "play_events")
    for name in ("target_type", "nd_album_id", "nd_artist_id", "target_data", "reverts_event_id", "mirror_pending"):
        op.drop_column("play_events", name)
