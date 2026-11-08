"""Saved assistant chats: the history of conversations survives closing the app."""
from alembic import op
import sqlalchemy as sa

revision = "d8a2f6c41e57"
down_revision = "c3e8f1a2d7b9"
branch_labels = depends_on = None


def upgrade():
    op.create_table("assistant_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assistant_chats_user_id", "assistant_chats", ["user_id"])


def downgrade():
    op.drop_table("assistant_chats")
