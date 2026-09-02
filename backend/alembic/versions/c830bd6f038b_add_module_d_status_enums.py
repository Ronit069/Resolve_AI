"""add_module_d_status_enums

Revision ID: c830bd6f038b
Revises: 4119534d8208
Create Date: 2026-08-29 17:23:01.091864

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c830bd6f038b'
down_revision: Union[str, None] = '4119534d8208'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use autocommit block for ALTER TYPE ADD VALUE
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE evidenceprocessingstatus ADD VALUE IF NOT EXISTS 'OCR_QUEUED'")
        op.execute("ALTER TYPE evidenceprocessingstatus ADD VALUE IF NOT EXISTS 'OCR_PROCESSING'")
        op.execute("ALTER TYPE evidenceprocessingstatus ADD VALUE IF NOT EXISTS 'EXTRACTED'")
        op.execute("ALTER TYPE evidenceprocessingstatus ADD VALUE IF NOT EXISTS 'REVIEW_REQUIRED'")
        op.execute("ALTER TYPE evidenceprocessingstatus ADD VALUE IF NOT EXISTS 'OCR_FAILED'")
        op.execute("ALTER TYPE evidenceprocessingstatus ADD VALUE IF NOT EXISTS 'REPROCESS_REQUESTED'")

def downgrade() -> None:
    # Downgrading enums in Postgres is non-trivial and often skipped or done by recreating the type
    pass
