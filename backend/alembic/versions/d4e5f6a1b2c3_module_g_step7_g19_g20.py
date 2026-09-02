"""module_g_step7_g19_g20

Revision ID: d4e5f6a1b2c3
Revises: a1b2c3d4e5f6
Create Date: 2026-09-01 09:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.models.module_d

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a1b2c3'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add guardrail_version to response_generation_runs (G-20 lineage)
    # Using server_default ensures existing records get a value
    op.add_column('response_generation_runs', sa.Column('guardrail_version', sa.String(length=50), server_default='v1', nullable=False))
    
    # Create G-19 evaluation tables
    op.create_table(
        'rag_evaluation_queries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('expected_chunk_ids', app.models.module_d.JSONVariant(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_table(
        'rag_evaluation_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('k_value', sa.Integer(), nullable=False),
        sa.Column('hit_rate', sa.Numeric(), nullable=True),
        sa.Column('precision_at_k', sa.Numeric(), nullable=True),
        sa.Column('run_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('rag_evaluation_runs')
    op.drop_table('rag_evaluation_queries')
    op.drop_column('response_generation_runs', 'guardrail_version')
