"""Keep playlist order when a download needs manual review."""

import sqlalchemy as sa
from alembic import op

revision = "6a3d8c2e710f"
down_revision = "f6a9b2d4e810"
branch_labels = depends_on = None


def upgrade():
    op.add_column("pending_playlist_adds", sa.Column("applied_song_id", sa.String(64)))


def downgrade():
    op.drop_column("pending_playlist_adds", "applied_song_id")
