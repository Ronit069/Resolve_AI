"""add password_hash to app_users for real auth

Revision ID: ac4059d442a7
Revises: eba2ccf9f91a
Create Date: 2026-10-17 13:54:09.354294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac4059d442a7'
down_revision: Union[str, None] = 'eba2ccf9f91a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only the password_hash column -- autogenerate also proposed dropping
    # idx_knowledge_chunks_embedding (an HNSW pgvector index that exists in
    # the real DB but isn't expressible in the ORM model metadata the same
    # way, so autogenerate sees it as "missing" and wants to drop it). That
    # is unrelated to this migration's purpose and never belongs in it.
    op.add_column('app_users', sa.Column('password_hash', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('app_users', 'password_hash')
