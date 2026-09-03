import inspect
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.config import settings
from app.models.shared import Merchant, Case
from app.models.module_g import (
    ResponseGenerationRun, GeneratedDraft, DraftClaim,
    GuardrailStatus, ClaimType, SupportStatus,
)
from app.services.external_action.contest_summary_gate import (
    validate_contest_summary,
    ContestSummaryGateErrorCode,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_12_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"

MAX_LEN = settings.SUMMARY_MAX_LENGTH


@pytest.fixture(scope="module")
def postgres_engine():
    import sqlalchemy as sa
    try:
        engine_default = sa.create_engine(DB_URL, isolation_level="AUTOCOMMIT")
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except Exception as e:
        pytest.skip(f"PostgreSQL not available: {e}")

    engine_test = sa.create_engine(TEST_DB_URL)
    yield engine_test

    engine_test.dispose()
    try:
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
    except Exception:
        pass
    engine_default.dispose()


@pytest.fixture(scope="module")
def alembic_engine(postgres_engine):
    from alembic.config import Config
    from alembic import command
    import os

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ini_path = os.path.join(base_dir, "alembic.ini")

    config = Config(ini_path)
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)
    config.set_main_option("script_location", os.path.join(base_dir, "alembic"))

    command.upgrade(config, "head")
    yield postgres_engine


@pytest.fixture
def db(alembic_engine):
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=alembic_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        for table in reversed(Base.metadata.sorted_tables):
            try:
                with alembic_engine.connect() as conn:
                    conn.execute(table.delete())
                    conn.commit()
            except Exception:
                pass


def setup_case(db: Session):
    merchant = Merchant(name="Test Merchant H12", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.commit()
    return merchant, case


def make_draft(db: Session, case: Case, guardrail_status=GuardrailStatus.PASS, is_current=True, summary="Original LLM summary"):
    gen_run = ResponseGenerationRun(
        case_id=case.case_id,
        prompt_template_version="v1",
        llm_model_version="gpt-4o-mini",
    )
    db.add(gen_run)
    db.flush()

    draft = GeneratedDraft(
        generation_run_id=gen_run.id,
        case_id=case.case_id,
        summary=summary,
        draft_json={"summary": summary},
        guardrail_status=guardrail_status,
        is_current=is_current,
    )
    db.add(draft)
    db.commit()
    return draft


def make_claim(db: Session, draft: GeneratedDraft, support_status=SupportStatus.SUPPORTED, claim_text="claim"):
    claim = DraftClaim(
        draft_id=draft.id,
        claim_text=claim_text,
        claim_type=ClaimType.CASE_FACT,
        support_status=support_status,
    )
    db.add(claim)
    db.commit()
    return claim


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. Valid candidate, PASS guardrail, no bad claims => allowed.
def test_valid_summary_allowed(db):
    merchant, case = setup_case(db)
    draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS)
    make_claim(db, draft, support_status=SupportStatus.SUPPORTED)

    result = validate_contest_summary(db, case.case_id, "A perfectly fine contest summary.", current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.error_code is None
    assert result.draft_id == draft.id


# 2. Candidate exactly at max length => allowed (boundary).
def test_summary_at_max_length_allowed(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    candidate = "x" * MAX_LEN
    result = validate_contest_summary(db, case.case_id, candidate, current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.candidate_summary_length == MAX_LEN


# 3. Candidate one over max length => blocked.
def test_summary_over_max_length_blocked(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    candidate = "x" * (MAX_LEN + 1)
    result = validate_contest_summary(db, case.case_id, candidate, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID


# 4. None candidate => blocked.
def test_none_summary_blocked(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    result = validate_contest_summary(db, case.case_id, None, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID


# 5. Empty string candidate => blocked.
def test_empty_summary_blocked(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    result = validate_contest_summary(db, case.case_id, "", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID


# 6. Whitespace-only candidate => blocked.
def test_whitespace_only_summary_blocked(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    result = validate_contest_summary(db, case.case_id, "   \n\t  ", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID


# 7. No current draft for case (candidate otherwise valid) => blocked.
def test_no_current_draft_blocked(db):
    merchant, case = setup_case(db)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID
    assert result.draft_id is None


# 8. guardrail_status == REVIEW => blocked.
def test_guardrail_review_blocked(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.REVIEW)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID
    assert result.guardrail_status == "REVIEW"


# 9. guardrail_status == FAIL => blocked.
def test_guardrail_fail_blocked(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.FAIL)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID
    assert result.guardrail_status == "FAIL"


# 10. PASS guardrail but one UNSUPPORTED claim => blocked.
def test_unsupported_claim_blocked(db):
    merchant, case = setup_case(db)
    draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS)
    make_claim(db, draft, support_status=SupportStatus.UNSUPPORTED)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID
    assert result.unsupported_claim_count == 1


# 11. PASS guardrail but one CONFLICT claim => blocked.
def test_conflict_claim_blocked(db):
    merchant, case = setup_case(db)
    draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS)
    make_claim(db, draft, support_status=SupportStatus.CONFLICT)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID
    assert result.unsupported_claim_count == 1


# 12. PASS guardrail, all claims SUPPORTED (and zero-claims case) => allowed.
def test_all_supported_claims_allowed(db):
    merchant, case = setup_case(db)
    draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS)
    make_claim(db, draft, support_status=SupportStatus.SUPPORTED)
    make_claim(db, draft, support_status=SupportStatus.SUPPORTED)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is True


def test_zero_claims_allowed(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.unsupported_claim_count == 0


# 13. Non-current draft with bad guardrail status is ignored; current draft governs.
def test_only_current_draft_governs(db):
    merchant, case = setup_case(db)
    old_draft = make_draft(db, case, guardrail_status=GuardrailStatus.FAIL, is_current=False)
    current_draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS, is_current=True)

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.draft_id == current_draft.id
    assert result.draft_id != old_draft.id


# 14. Freshness (guardrail): mutate guardrail_status via a separate connection.
def test_fresh_read_guardrail_status(db, alembic_engine):
    merchant, case = setup_case(db)
    draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    loaded = db.query(GeneratedDraft).filter(GeneratedDraft.id == draft.id).first()
    assert loaded.guardrail_status == GuardrailStatus.PASS

    import sqlalchemy as sa
    with alembic_engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE generated_drafts SET guardrail_status = 'FAIL' WHERE id = :id"),
            {"id": str(draft.id)},
        )
        conn.commit()

    assert loaded.guardrail_status == GuardrailStatus.PASS  # stale in-memory object

    result = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.guardrail_status == "FAIL"


# 15. Freshness (claims): insert a bad claim via a separate connection after the
# session already queried the (empty) claim set once.
def test_fresh_read_claims(db, alembic_engine):
    merchant, case = setup_case(db)
    draft = make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    # First call: no claims yet, should be allowed.
    result1 = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result1.allowed is True

    import sqlalchemy as sa
    with alembic_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO draft_claims (id, draft_id, claim_text, claim_type, support_status) "
                "VALUES (:id, :draft_id, 'late claim', 'CASE_FACT', 'UNSUPPORTED')"
            ),
            {"id": str(uuid.uuid4()), "draft_id": str(draft.id)},
        )
        conn.commit()

    result2 = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert result2.allowed is False
    assert result2.unsupported_claim_count == 1


# 16. Signature makes caller-supplied draft/guardrail/claim data structurally impossible.
def test_signature_accepts_no_draft_or_guardrail_override():
    sig = inspect.signature(validate_contest_summary)
    param_names = set(sig.parameters.keys())
    assert param_names == {"db", "case_id", "candidate_summary", "current_time"}
    assert "draft_id" not in param_names
    assert "guardrail_status" not in param_names
    assert "support_status" not in param_names


# 17. No external-action capability in the module.
def test_no_external_action_capability_in_module():
    import app.services.external_action.contest_summary_gate as gate_module
    source = inspect.getsource(gate_module).lower()
    for forbidden in ("import requests", "import httpx", "import urllib", ".get(\"http", ".post(\"http"):
        assert forbidden not in source, f"unexpected outbound-call marker '{forbidden}' found in contest_summary_gate.py"


# 18. Exact error code string equality.
def test_error_code_exact_string():
    assert ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID == "CONTEST_SUMMARY_INVALID"


# 19. Deterministic behavior.
def test_deterministic_behavior(db):
    merchant, case = setup_case(db)
    make_draft(db, case, guardrail_status=GuardrailStatus.PASS)

    r1 = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    r2 = validate_contest_summary(db, case.case_id, "A fine summary.", current_time=FIXED_NOW)
    assert r1.allowed == r2.allowed == True
    assert r1.checked_at == r2.checked_at == FIXED_NOW
