"""module_g_step6_g05

Revision ID: a1b2c3d4e5f6
Revises: cdeb574db469
Create Date: 2026-08-31 23:03:00.000000

Tables G7-G10:
  G7  response_generation_runs
  G8  generated_drafts
  G9  draft_claims
  G10 llm_guardrail_results
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.models.module_d


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'cdeb574db469'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- G7: response_generation_runs ---
    op.create_table(
        'response_generation_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('retrieval_run_id', sa.UUID(), nullable=True),
        sa.Column('prompt_template_version', sa.String(length=50), nullable=False),
        sa.Column('llm_model_version', sa.String(length=100), nullable=False),
        sa.Column('fact_packet_hash', sa.String(length=64), nullable=True),
        sa.Column('status', sa.Enum('RUNNING', 'PASS', 'FAILED', name='generationstatus'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['cases.case_id']),
        sa.ForeignKeyConstraint(['retrieval_run_id'], ['rag_retrieval_runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_response_generation_runs_case_id'), 'response_generation_runs', ['case_id'], unique=False)
    op.create_index(op.f('ix_response_generation_runs_retrieval_run_id'), 'response_generation_runs', ['retrieval_run_id'], unique=False)

    # --- G8: generated_drafts ---
    op.create_table(
        'generated_drafts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('generation_run_id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('contest_amount_minor', sa.Numeric(), nullable=True),
        sa.Column('draft_json', app.models.module_d.JSONVariant(), nullable=False),
        sa.Column('guardrail_status', sa.Enum('PASS', 'REVIEW', 'FAIL', name='guardrailstatus'), nullable=False),
        sa.Column('is_current', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['cases.case_id']),
        sa.ForeignKeyConstraint(['generation_run_id'], ['response_generation_runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_generated_drafts_case_id'), 'generated_drafts', ['case_id'], unique=False)
    op.create_index(op.f('ix_generated_drafts_generation_run_id'), 'generated_drafts', ['generation_run_id'], unique=False)

    # --- G9: draft_claims ---
    op.create_table(
        'draft_claims',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('draft_id', sa.UUID(), nullable=False),
        sa.Column('claim_text', sa.Text(), nullable=False),
        sa.Column('claim_type', sa.Enum('CASE_FACT', 'POLICY', 'RECOMMENDATION', name='claimtype'), nullable=False),
        sa.Column('support_status', sa.Enum('SUPPORTED', 'UNSUPPORTED', 'CONFLICT', name='supportstatus'), nullable=False),
        sa.Column('fact_refs', app.models.module_d.JSONVariant(), nullable=True),
        sa.Column('chunk_refs', app.models.module_d.JSONVariant(), nullable=True),
        sa.ForeignKeyConstraint(['draft_id'], ['generated_drafts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_draft_claims_draft_id'), 'draft_claims', ['draft_id'], unique=False)

    # --- G10: llm_guardrail_results ---
    op.create_table(
        'llm_guardrail_results',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('draft_id', sa.UUID(), nullable=False),
        sa.Column('check_type', sa.Enum(
            'SCHEMA', 'GROUNDING', 'CONTRADICTION', 'PII',
            'PROMPT_INJECTION', 'CITATION_COVERAGE', 'LENGTH',
            name='guardrailchecktype'), nullable=False),
        sa.Column('result', sa.String(length=20), nullable=False),
        sa.Column('details_json', app.models.module_d.JSONVariant(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['draft_id'], ['generated_drafts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_llm_guardrail_results_draft_id'), 'llm_guardrail_results', ['draft_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_llm_guardrail_results_draft_id'), table_name='llm_guardrail_results')
    op.drop_table('llm_guardrail_results')
    op.drop_index(op.f('ix_draft_claims_draft_id'), table_name='draft_claims')
    op.drop_table('draft_claims')
    op.drop_index(op.f('ix_generated_drafts_generation_run_id'), table_name='generated_drafts')
    op.drop_index(op.f('ix_generated_drafts_case_id'), table_name='generated_drafts')
    op.drop_table('generated_drafts')
    op.drop_index(op.f('ix_response_generation_runs_retrieval_run_id'), table_name='response_generation_runs')
    op.drop_index(op.f('ix_response_generation_runs_case_id'), table_name='response_generation_runs')
    op.drop_table('response_generation_runs')
    # Drop enums
    op.execute("DROP TYPE IF EXISTS generationstatus")
    op.execute("DROP TYPE IF EXISTS guardrailstatus")
    op.execute("DROP TYPE IF EXISTS claimtype")
    op.execute("DROP TYPE IF EXISTS supportstatus")
    op.execute("DROP TYPE IF EXISTS guardrailchecktype")
