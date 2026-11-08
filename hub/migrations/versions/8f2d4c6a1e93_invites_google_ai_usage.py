"""invites, Google sign-in, AI spending per user

Revision ID: 8f2d4c6a1e93
Revises: 5e0a9d1c7b42
Create Date: 2026-10-20 02:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f2d4c6a1e93'
down_revision: Union[str, None] = '5e0a9d1c7b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('google_sub', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('google_email', sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column('last_seen_at', sa.DateTime(), nullable=True))
        batch_op.create_unique_constraint('uq_users_google_sub', ['google_sub'])

    op.create_table('invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('note', sa.String(length=128), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('used_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['used_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )

    op.create_table('ai_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_usage_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_usage_created_at'), ['created_at'], unique=False)

    # Spending so far: recognition tasks kept their cost (chat turns did only when they proposed actions).
    op.execute(
        "INSERT INTO ai_usage (user_id, kind, tokens_in, tokens_out, cost_usd, created_at) "
        "SELECT user_id, input_kind, COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), COALESCE(cost_usd, 0), created_at "
        "FROM assistant_tasks WHERE COALESCE(cost_usd, 0) > 0 OR COALESCE(tokens_in, 0) > 0"
    )


def downgrade() -> None:
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_usage_created_at'))
        batch_op.drop_index(batch_op.f('ix_ai_usage_user_id'))
    op.drop_table('ai_usage')
    op.drop_table('invites')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_google_sub', type_='unique')
        batch_op.drop_column('last_seen_at')
        batch_op.drop_column('google_email')
        batch_op.drop_column('google_sub')
