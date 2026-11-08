"""Persistent daily assistant message allowance."""

import sqlalchemy as sa
from alembic import op

revision = "f6a9b2d4e810"
down_revision = "d8a2f6c41e57"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "assistant_daily_usage",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("messages", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("assistant_daily_usage")
