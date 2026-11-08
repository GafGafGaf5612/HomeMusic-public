"""Registration by email: the users' confirmed email, codes sent by letter."""
from alembic import op
import sqlalchemy as sa

revision = "c3e8f1a2d7b9"
down_revision = "b61d9a7e4c20"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(length=320), nullable=True))
        batch_op.create_unique_constraint("uq_users_email", ["email"])
    op.create_table("email_codes",
                    sa.Column("id", sa.Integer(), nullable=False),
                    sa.Column("email", sa.String(length=320), nullable=False),
                    sa.Column("code_hash", sa.String(length=64), nullable=False),
                    sa.Column("created_at", sa.DateTime(), nullable=False),
                    sa.Column("expires_at", sa.DateTime(), nullable=False),
                    sa.Column("attempts", sa.Integer(), nullable=False),
                    sa.Column("used_at", sa.DateTime(), nullable=True),
                    sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_email_codes_email", "email_codes", ["email"])
    op.create_index("ix_email_codes_created_at", "email_codes", ["created_at"])


def downgrade():
    op.drop_index("ix_email_codes_created_at", "email_codes")
    op.drop_index("ix_email_codes_email", "email_codes")
    op.drop_table("email_codes")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("uq_users_email", type_="unique")
        batch_op.drop_column("email")
