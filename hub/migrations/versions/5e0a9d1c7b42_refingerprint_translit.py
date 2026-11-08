"""recompute track fingerprints: transliteration folds щ (shh/shch) and ё (yo)

Revision ID: 5e0a9d1c7b42
Revises: c1b8b547387b
Create Date: 2026-10-18 20:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5e0a9d1c7b42'
down_revision: Union[str, None] = 'c1b8b547387b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.matching.normalize import fingerprint

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, artist, title, fingerprint FROM tracks")).all()
    for id_, artist, title, old in rows:
        new = fingerprint(artist or "", title or "")
        if new != old:
            conn.execute(sa.text("UPDATE tracks SET fingerprint = :fp WHERE id = :id"), {"fp": new, "id": id_})


def downgrade() -> None:
    pass  # fingerprints are derived data; the old folding is gone
