"""Admin insights, stage 1 (INSIGHTS.md 6.1): wave session history, what a wave handed out, list launches.
Expanding only: no row is moved or rewritten."""
from alembic import op
import sqlalchemy as sa

revision = "c5f1a8d3e2b7"
down_revision = "6a3d8c2e710f"
branch_labels = depends_on = None

WAVE_COLUMNS = ("origin", "client", "restart_of", "taste_model", "request_json", "next_calls", "cleared_at")


def upgrade():
    op.add_column("wave_sessions", sa.Column("origin", sa.String(32)))
    op.add_column("wave_sessions", sa.Column("client", sa.String(32)))
    op.add_column("wave_sessions", sa.Column("restart_of", sa.String(32)))
    op.add_column("wave_sessions", sa.Column("taste_model", sa.String(8)))
    op.add_column("wave_sessions", sa.Column("request_json", sa.JSON()))
    op.add_column("wave_sessions", sa.Column("next_calls", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("wave_sessions", sa.Column("cleared_at", sa.DateTime()))
    op.create_index("ix_wave_sessions_user_created", "wave_sessions", ["user_id", "created_at"])
    op.create_table(
        "wave_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("first_handed_at", sa.DateTime(), nullable=False),
        sa.Column("last_handed_at", sa.DateTime(), nullable=False),
        sa.Column("handed_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("nd_song_id", sa.String(64)),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("artist", sa.String(256), nullable=False, server_default=""),
        sa.Column("title", sa.String(256), nullable=False, server_default=""),
        sa.Column("generator", sa.String(32), nullable=False),
        sa.Column("gens", sa.JSON()),
        sa.Column("familiar", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sim", sa.Float()),
        sa.Column("value", sa.Float()),
        sa.ForeignKeyConstraint(["session_id"], ["wave_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "ref", name="uq_wave_items_session_ref"),
    )
    op.create_index("ix_wave_items_session_id", "wave_items", ["session_id"])
    op.create_index("ix_wave_items_user_id", "wave_items", ["user_id"])
    op.add_column("play_events", sa.Column("launch_id", sa.String(64)))
    op.add_column("play_events", sa.Column("launch_origin", sa.String(32)))
    op.create_index("ix_play_events_user_launch", "play_events", ["user_id", "launch_id"])


def downgrade():
    op.drop_index("ix_play_events_user_launch", "play_events")
    for name in ("launch_origin", "launch_id"):
        op.drop_column("play_events", name)
    op.drop_index("ix_wave_items_user_id", "wave_items")
    op.drop_index("ix_wave_items_session_id", "wave_items")
    op.drop_table("wave_items")
    op.drop_index("ix_wave_sessions_user_created", "wave_sessions")
    for name in reversed(WAVE_COLUMNS):
        op.drop_column("wave_sessions", name)
