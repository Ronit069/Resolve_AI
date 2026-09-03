"""module_k_dispute_outcome_source_event_id_unique

Revision ID: a7f3c9d2e1b4
Revises: c1d2e3f4a5b6
Create Date: 2026-09-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7f3c9d2e1b4'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Module K: idempotency for the outcome webhook is enforced at the DB
    # layer — source_event_id comes from the X-Razorpay-Event-Id header and
    # must never produce a second DisputeOutcome row for the same event.
    op.create_unique_constraint(
        'uq_dispute_outcomes_source_event_id', 'dispute_outcomes', ['source_event_id']
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_dispute_outcomes_source_event_id', 'dispute_outcomes', type_='unique'
    )
