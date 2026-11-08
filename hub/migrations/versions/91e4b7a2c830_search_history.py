"""Account-wide recent search queries."""

import sqlalchemy as sa
from alembic import op

revision = "91e4b7a2c830"
down_revision = "c5f1a8d3e2b7"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "search_history",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("query_key", sa.String(1024), primary_key=True),
        sa.Column("query", sa.String(512), nullable=False),
        sa.Column("searched_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("search_history")
