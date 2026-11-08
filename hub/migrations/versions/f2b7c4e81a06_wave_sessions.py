"""«Моя волна»: wave sessions (MIXES.md 7)

Revision ID: f2b7c4e81a06
Revises: e7a1c5d93b20
Create Date: 2026-10-23 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2b7c4e81a06'
down_revision: Union[str, None] = 'e7a1c5d93b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('wave_sessions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('seed_json', sa.JSON(), nullable=True),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('state_json', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['batch_id'], ['download_batches.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('wave_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_wave_sessions_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_table('wave_sessions')
