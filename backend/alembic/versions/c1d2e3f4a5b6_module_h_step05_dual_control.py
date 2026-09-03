"""module_h_step05_dual_control

Revision ID: c1d2e3f4a5b6
Revises: b224a21ac9b0
Create Date: 2026-09-02 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b224a21ac9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # H-05 dual control: new queue state for cases awaiting a second, distinct
    # APPROVER before a gated APPROVE_CONTEST/APPROVE_ACCEPT decision finalizes.
    op.execute("ALTER TYPE queuestatus ADD VALUE IF NOT EXISTS 'PENDING_SECOND_APPROVAL'")
    op.add_column(
        'review_queue_items',
        sa.Column('pending_review_action_id', sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        'fk_review_queue_items_pending_review_action_id',
        'review_queue_items', 'review_actions',
        ['pending_review_action_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_review_queue_items_pending_review_action_id',
        'review_queue_items', type_='foreignkey'
    )
    op.drop_column('review_queue_items', 'pending_review_action_id')
    # Postgres does not support removing a value from an enum type; downgrading
    # the enum itself is intentionally not attempted here.
