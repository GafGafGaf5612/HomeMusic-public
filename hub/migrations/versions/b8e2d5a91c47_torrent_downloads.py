"""Releases from a torrent tracker downloaded by qBittorrent (README, «Раздачи с rutracker»)"""

import sqlalchemy as sa
from alembic import op

revision = "b8e2d5a91c47"
down_revision = "a3c8e5f17b92"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "torrent_downloads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("info_hash", sa.String(40), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("details_url", sa.String(1024)),
        sa.Column("album_ref", sa.String(64)),
        sa.Column("files_json", sa.JSON(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="downloading"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(32)),
        sa.Column("seeds", sa.Integer()),
        sa.Column("speed", sa.Integer()),
        sa.Column("eta_s", sa.Integer()),
        sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("album_id", sa.Integer(), sa.ForeignKey("albums.id", ondelete="SET NULL")),
        sa.Column("error", sa.Text()),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_torrent_downloads_user_id", "torrent_downloads", ["user_id"])
    op.create_index("ix_torrent_downloads_info_hash", "torrent_downloads", ["info_hash"])
    op.create_index("ix_torrent_downloads_status", "torrent_downloads", ["status"])


def downgrade():
    op.drop_table("torrent_downloads")
