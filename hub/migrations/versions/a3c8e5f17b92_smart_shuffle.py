"""Smart shuffle: sessions, handed-out recommendations, tracks hidden per context (MIXES.md 8)"""

import sqlalchemy as sa
from alembic import op

revision = "a3c8e5f17b92"
down_revision = "91e4b7a2c830"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "smart_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("context", sa.String(80), nullable=False),
        sa.Column("title", sa.String(256)),
        sa.Column("own_count", sa.Integer(), nullable=False),
        sa.Column("per_rec", sa.Integer(), nullable=False),
        sa.Column("first_id", sa.String(80)),
        sa.Column("taste_model", sa.String(8)),
        sa.Column("origin", sa.String(32)),
        sa.Column("client", sa.String(32)),
        sa.Column("next_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_smart_sessions_user_id", "smart_sessions", ["user_id"])
    op.create_index("ix_smart_sessions_user_context", "smart_sessions", ["user_id", "context", "created_at"])
    op.create_table(
        "smart_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("smart_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("handed_at", sa.DateTime(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("nd_song_id", sa.String(64)),
        sa.Column("fingerprint", sa.String(1024), nullable=False, server_default=""),
        sa.Column("artist", sa.String(256), nullable=False, server_default=""),
        sa.Column("title", sa.String(256), nullable=False, server_default=""),
        sa.Column("generator", sa.String(32), nullable=False),
        sa.Column("gens", sa.JSON()),
        sa.Column("familiar", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sim", sa.Float()),
        sa.Column("value", sa.Float()),
        sa.Column("state", sa.String(12), nullable=False, server_default="handed"),
        sa.UniqueConstraint("session_id", "ref", name="uq_smart_items_session_ref"),
    )
    op.create_index("ix_smart_items_session_id", "smart_items", ["session_id"])
    op.create_index("ix_smart_items_user_id", "smart_items", ["user_id"])
    op.create_table(
        "smart_hidden",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context", sa.String(80), nullable=False),
        sa.Column("fingerprint", sa.String(1024), nullable=False),
        sa.Column("ref", sa.String(64)),
        sa.Column("nd_song_id", sa.String(64)),
        sa.Column("artist", sa.String(256), nullable=False, server_default=""),
        sa.Column("title", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "context", "fingerprint", name="uq_smart_hidden"),
    )
    op.create_index("ix_smart_hidden_user_id", "smart_hidden", ["user_id"])


def downgrade():
    op.drop_table("smart_hidden")
    op.drop_table("smart_items")
    op.drop_table("smart_sessions")
