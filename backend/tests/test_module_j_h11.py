"""
Module J, Category D — H-11 (minimum evidence for submit) tests.

Covers both the pure gate (check_minimum_evidence_for_submit) in
isolation and its wiring into outbox_writer.write_outbox_for_package
(invoked only when H-10 has determined action == "submit").
"""

import uuid
import itertools
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_a import Dispute
from app.models.module_c import EvidenceDocument, EvidenceType, ScanStatus, EvidenceProcessingStatus
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_g import ResponseGenerationRun, GeneratedDraft, GenerationStatus, GuardrailStatus
from app.models.module_h import (
    ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum,
    ContestPackage, ContestPackageDocument, ContestPackageStatus,
    ExternalActionOutbox, ExternalActionType,
)

from app.services.external_action.contest_package_assembly import assemble_contest_package
from app.services.external_action.outbox_writer import write_outbox_for_package
from app.services.external_action.evidence_completeness_gate import (
    EvidenceCompletenessGateErrorCode,
    check_minimum_evidence_for_submit,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_j_h11_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"


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


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
_policy_version_counter = itertools.count(1)


def make_merchant(db: Session):
    merchant = Merchant(name="Test Merchant J-H11", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    return merchant


def make_prediction(db: Session, case, recommendation="CONTEST", hard_block=False):
    policy_version = EvidencePolicyVersion(
        payment_network="visa", reason_code="fraud", phase="pre",
        version=next(_policy_version_counter),
        effective_from=datetime.now(timezone.utc),
    )
    db.add(policy_version)

    model_version = ModelVersion(algorithm="lgbm", status="active")
    db.add(model_version)
    db.flush()

    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id)
    db.add(decision_policy)
    db.flush()

    validation_run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=policy_version.policy_version_id,
        status=EValidationRunStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"val_{uuid.uuid4()}",
    )
    db.add(validation_run)
    db.flush()

    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=validation_run.id,
        feature_schema_version="v1",
        feature_hash="hash",
        features_json={"amount": 1000},
        is_current=True,
    )
    db.add(snapshot)
    db.flush()

    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=0.9,
        calibrated_probability=0.9,
        recommendation=recommendation,
        hard_block=hard_block,
        idempotency_key=f"pred_{uuid.uuid4()}",
    )
    db.add(prediction)
    db.flush()
    return prediction


def make_evidence_document(db: Session, case, merchant, evidence_type=EvidenceType.PROOF_OF_DELIVERY, scan_status=ScanStatus.CLEAN):
    doc = EvidenceDocument(
        case_id=case.case_id,
        merchant_id=merchant.merchant_id,
        evidence_type=evidence_type.value,
        object_key=f"key_{uuid.uuid4()}",
        original_filename="proof.pdf",
        mime_type="application/pdf",
        file_size_bytes=1000,
        sha256=hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
        scan_status=scan_status,
        processing_status=EvidenceProcessingStatus.READY_FOR_OCR,
    )
    db.add(doc)
    db.flush()
    return doc


def make_draft(db: Session, case, contest_amount_minor=5000, summary="Evidence supports contest.", guardrail_status=GuardrailStatus.PASS):
    run = ResponseGenerationRun(
        case_id=case.case_id,
        prompt_template_version="v1",
        llm_model_version="test-model",
        guardrail_version="v1",
        status=GenerationStatus.PASS,
    )
    db.add(run)
    db.flush()

    draft = GeneratedDraft(
        generation_run_id=run.id,
        case_id=case.case_id,
        summary=summary,
        contest_amount_minor=contest_amount_minor,
        draft_json={"summary": summary},
        guardrail_status=guardrail_status,
        is_current=True,
    )
    db.add(draft)
    db.flush()
    return draft


def make_finalized_case(
    db: Session,
    merchant,
    dispute_amount_minor=10000,
    contest_amount_minor=5000,
    n_documents=1,
    finalizing_action=ReviewActionEnum.APPROVE_CONTEST,
    queue_status=QueueStatus.DONE,
):
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id=case.external_dispute_id,
        payment_id=f"pay_{uuid.uuid4()}",
        amount_minor=dispute_amount_minor,
        currency="INR",
        reason_code="fraud",
        status="open",
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(dispute)
    db.flush()

    prediction = make_prediction(db, case)

    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=100,
        queue_status=queue_status,
        respond_by=dispute.respond_by,
    )
    db.add(queue_item)
    db.flush()

    reviewer = AppUser(merchant_id=merchant.merchant_id, email=f"reviewer_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    db.add(reviewer)
    db.flush()

    review_action = ReviewAction(
        queue_item_id=queue_item.id,
        case_id=case.case_id,
        reviewer_id=reviewer.user_id,
        action=finalizing_action,
    )
    db.add(review_action)
    db.flush()

    draft = make_draft(db, case, contest_amount_minor=contest_amount_minor)

    documents = [make_evidence_document(db, case, merchant) for _ in range(n_documents)]

    db.commit()
    return {"case": case, "dispute": dispute, "queue_item": queue_item, "review_action": review_action, "draft": draft, "documents": documents}


# 1. Pure gate: zero approved documents fails closed
def test_gate_fails_with_zero_approved_documents(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=0)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    result = check_minimum_evidence_for_submit(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == EvidenceCompletenessGateErrorCode.EVIDENCE_MINIMUM_NOT_MET
    assert result.approved_document_count == 0


# 2. Pure gate: at least one approved document passes
def test_gate_passes_with_one_approved_document(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    result = check_minimum_evidence_for_submit(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.error_code is None
    assert result.approved_document_count == 1


# 3. Pure gate: unknown contest_package_id fails closed
def test_gate_fails_when_package_not_found(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)

    result = check_minimum_evidence_for_submit(db, fx["case"].case_id, uuid.uuid4(), current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == EvidenceCompletenessGateErrorCode.EVIDENCE_MINIMUM_NOT_MET
    assert result.approved_document_count is None


# 4. Pure gate: only approved=True documents count
def test_gate_ignores_unapproved_documents(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    pkg_doc = db.query(ContestPackageDocument).filter(ContestPackageDocument.contest_package_id == assembly.contest_package_id).first()
    pkg_doc.approved = False
    db.commit()

    result = check_minimum_evidence_for_submit(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.approved_document_count == 0


# 5. Pure gate: fails closed on unexpected DB error
def test_gate_fails_closed_on_unexpected_error(db, monkeypatch):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    original_query = db.query

    def boom(*args, **kwargs):
        if args and args[0] is ContestPackage:
            raise RuntimeError("simulated unexpected DB failure")
        return original_query(*args, **kwargs)

    monkeypatch.setattr(db, "query", boom)
    result = check_minimum_evidence_for_submit(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == EvidenceCompletenessGateErrorCode.EVIDENCE_MINIMUM_NOT_MET
    assert "unexpected" in result.reason.lower()


# 6. Integration: outbox_writer blocks the entire write (all-or-nothing) when
# H-10 resolves to "submit" but zero documents are approved.
def test_outbox_writer_blocks_submit_with_zero_documents(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=0)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    assert len(assembly.documents) == 0

    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is False
    assert result.gate_summary.h10_action == "submit"
    assert result.gate_summary.h11_allowed is False
    assert "EVIDENCE" in (result.reason or "").upper() or "evidence" in (result.reason or "").lower()
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.case_id == fx["case"].case_id).count() == 0

    package = db.query(ContestPackage).filter(ContestPackage.id == assembly.contest_package_id).first()
    assert package.status == ContestPackageStatus.DRAFT  # unchanged, not APPROVED


# 7. Integration: H-11 is never consulted (and never blocks) when H-10 resolves to "draft".
def test_outbox_writer_h11_not_invoked_for_draft(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=0, queue_status=QueueStatus.PENDING)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is True
    assert result.gate_summary.h10_action == "draft"
    assert result.gate_summary.h11_allowed is True  # vacuously true; gate never consulted for draft
    assert result.gate_summary.h11_reason is None


# 8. Integration: submit succeeds and writes outbox rows when >=1 approved document exists.
def test_outbox_writer_allows_submit_with_one_document(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is True
    assert result.gate_summary.h11_allowed is True
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).count() == 1
