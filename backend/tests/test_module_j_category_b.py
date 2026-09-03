import pytest
import uuid
import itertools
import hashlib
from datetime import datetime, timezone, timedelta
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
    ExternalActionOutbox, ExternalActionAttempt, ExternalActionType, OutboxStatus,
)

from app.services.external_action.contest_package_assembly import assemble_contest_package
from app.services.external_action.outbox_writer import write_outbox_for_package
from app.worker.external_action_tasks import (
    dispatch_external_action_outbox,
    ExternalBoundaryNotImplemented,
    SimulatedTransientTransportFailure,
    SimulatedUnknownTransportResult,
    MAX_ATTEMPTS,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_j_cat_b_pg"
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


def make_merchant(db: Session, name="Test Merchant J-CatB"):
    merchant = Merchant(name=name, external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    return merchant


_policy_version_counter = itertools.count(1)


def make_prediction(db: Session, case: Case, recommendation="CONTEST", hard_block=False):
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


def make_evidence_document(db: Session, case: Case, merchant: Merchant, evidence_type=EvidenceType.PROOF_OF_DELIVERY, scan_status=ScanStatus.CLEAN):
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


def make_draft(db: Session, case: Case, contest_amount_minor=5000, summary="Evidence supports contest.", guardrail_status=GuardrailStatus.PASS):
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
    merchant: Merchant,
    dispute_amount_minor=10000,
    contest_amount_minor=5000,
    dispute_status="open",
    respond_by=None,
    summary="Evidence supports contest.",
    guardrail_status=GuardrailStatus.PASS,
    hard_block=False,
    recommendation="CONTEST",
    n_documents=1,
    document_evidence_types=None,
    document_scan_statuses=None,
):
    """
    Builds a case whose ReviewQueueItem is DONE with a finalized
    APPROVE_CONTEST ReviewAction — i.e. exactly the trigger state Category
    B's assembly/outbox-writer functions are meant to consume. Mirrors the
    fixture conventions established across test_module_h_step*.py.
    """
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
        status=dispute_status,
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=respond_by or (datetime.now(timezone.utc) + timedelta(days=1)),
    )
    db.add(dispute)
    db.flush()

    prediction = make_prediction(db, case, recommendation=recommendation, hard_block=hard_block)

    queue_item = ReviewQueueItem(
        case_id=case.case_id,
        prediction_id=prediction.id,
        priority_score=100,
        queue_status=QueueStatus.DONE,
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
        action=ReviewActionEnum.APPROVE_CONTEST,
    )
    db.add(review_action)
    db.flush()

    draft = make_draft(db, case, contest_amount_minor=contest_amount_minor, summary=summary, guardrail_status=guardrail_status)

    documents = []
    types = document_evidence_types or [EvidenceType.PROOF_OF_DELIVERY] * n_documents
    scans = document_scan_statuses or [ScanStatus.CLEAN] * n_documents
    for i in range(n_documents):
        documents.append(make_evidence_document(db, case, merchant, evidence_type=types[i], scan_status=scans[i]))

    db.commit()
    return {
        "case": case, "dispute": dispute, "prediction": prediction, "queue_item": queue_item,
        "review_action": review_action, "draft": draft, "documents": documents,
    }


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. Successful ContestPackage assembly
def test_successful_contest_package_assembly(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant)
    result = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    assert result.assembled is True
    assert result.contest_package_id is not None
    package = db.query(ContestPackage).filter(ContestPackage.id == result.contest_package_id).first()
    assert package.status == ContestPackageStatus.DRAFT
    assert package.contest_amount_minor == 5000
    assert package.summary == "Evidence supports contest."


# 2. Correct ContestPackageDocument creation
def test_contest_package_document_creation(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1, document_evidence_types=[EvidenceType.PROOF_OF_DELIVERY])
    result = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    assert len(result.documents) == 1
    assert result.documents[0].razorpay_evidence_field == "shipping_proof"
    pkg_docs = db.query(ContestPackageDocument).filter(ContestPackageDocument.contest_package_id == result.contest_package_id).all()
    assert len(pkg_docs) == 1
    assert pkg_docs[0].approved is True
    assert pkg_docs[0].razorpay_evidence_field == "shipping_proof"


# 3. H-08 evidence rejection (unsafe document excluded from package)
def test_h08_evidence_rejection_excludes_unsafe_document(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(
        db, merchant, n_documents=1,
        document_evidence_types=[EvidenceType.PROOF_OF_DELIVERY],
        document_scan_statuses=[ScanStatus.INFECTED],
    )
    result = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    assert result.assembled is True
    assert len(result.documents) == 0  # unsafe document never becomes a ContestPackageDocument


# 4. H-07 amount rejection at outbox-write time
def test_h07_amount_rejection_blocks_outbox_write(db):
    merchant = make_merchant(db)
    # contest_amount_minor exceeds dispute_amount_minor -> H-07 must block
    fx = make_finalized_case(db, merchant, dispute_amount_minor=1000, contest_amount_minor=5000)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    assert assembly.assembled is True
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is False
    assert result.gate_summary.h07_allowed is False
    assert db.query(ExternalActionOutbox).count() == 0
    package = db.query(ContestPackage).filter(ContestPackage.id == assembly.contest_package_id).first()
    assert package.status == ContestPackageStatus.DRAFT  # unchanged, not APPROVED


# 5. H-12 summary rejection at outbox-write time
def test_h12_summary_rejection_blocks_outbox_write(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, guardrail_status=GuardrailStatus.FAIL)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is False
    assert result.gate_summary.h12_allowed is False
    assert db.query(ExternalActionOutbox).count() == 0


# 6. H-06 non-actionable/deadline rejection at outbox-write time
def test_h06_deadline_rejection_blocks_outbox_write(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, respond_by=FIXED_NOW - timedelta(hours=1))
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is False
    assert result.gate_summary.h06_allowed is False
    assert db.query(ExternalActionOutbox).count() == 0


# 7. H-10 draft vs submit determination reflected in outbox action type
def test_h10_submit_action_produces_contest_submit(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is True
    assert result.gate_summary.h10_action == "submit"
    contest_rows = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).all()
    assert len(contest_rows) == 1


def test_h10_draft_action_when_not_finalized_approve_contest(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant)
    # Overwrite the finalizing action to something other than APPROVE_CONTEST
    # to force H-10 into "draft" — mirrors H-10's own test conventions.
    fx["review_action"].action = ReviewActionEnum.REJECT_RECOMMENDATION
    db.commit()

    # Assembly itself requires an APPROVE_CONTEST review_action, so exercise
    # write_outbox_for_package's H-10 read directly against a package
    # assembled from a case whose finalizing action is not APPROVE_CONTEST
    # is not reachable via assemble_contest_package (it would already have
    # rejected assembly). This test instead confirms determine_contest_submission_action
    # itself is consulted by re-pointing at the gate through the writer on
    # a manually constructed DRAFT-status package for a case whose queue
    # item is DONE but whose finalizing action is not APPROVE_CONTEST.
    from app.services.external_action.contest_submission_action_gate import determine_contest_submission_action
    action_result = determine_contest_submission_action(db, fx["case"].case_id, current_time=FIXED_NOW)
    assert action_result.action == "draft"


# 8. Fresh-read behavior: mutate underlying state via a separate session,
# assert the assembly/writer see the fresh value, not a stale one.
def test_fresh_read_behavior_on_dispute_amount(db, alembic_engine):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, dispute_amount_minor=10000, contest_amount_minor=5000)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    # Load the dispute into this session's identity map first.
    _ = db.query(Dispute).filter(Dispute.case_id == fx["case"].case_id).first()

    # Mutate via a separate raw connection/session, simulating a concurrent writer.
    from sqlalchemy.orm import sessionmaker
    OtherSession = sessionmaker(bind=alembic_engine)
    other = OtherSession()
    try:
        other_dispute = other.query(Dispute).filter(Dispute.case_id == fx["case"].case_id).first()
        other_dispute.amount_minor = 4000  # now less than contest_amount_minor
        other.commit()
    finally:
        other.close()

    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is False
    assert result.gate_summary.h07_allowed is False  # sees the fresh (lowered) amount, not the stale one


# 9. Transactional package/outbox creation
def test_transactional_outbox_creation_all_or_nothing(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=2)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    assert len(assembly.documents) == 2
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is True
    # 2 UPLOAD_DOCUMENT + 1 CONTEST_SUBMIT = 3 rows, all committed together
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.case_id == fx["case"].case_id).count() == 3


# 10. Deterministic package hash
def test_deterministic_package_hash(db):
    merchant_a = make_merchant(db, name="Merchant Hash A")
    merchant_b = make_merchant(db, name="Merchant Hash B")
    fx_a = make_finalized_case(db, merchant_a, summary="Same summary text.", contest_amount_minor=7000)
    fx_b = make_finalized_case(db, merchant_b, summary="Same summary text.", contest_amount_minor=7000)

    result_a = assemble_contest_package(db, fx_a["case"].case_id, fx_a["review_action"].id, current_time=FIXED_NOW)
    result_b = assemble_contest_package(db, fx_b["case"].case_id, fx_b["review_action"].id, current_time=FIXED_NOW)

    # Different case_id/review_action_id/draft_id -> different hash (hash is
    # scoped to the specific package's identity, not just its content).
    assert result_a.package_hash != result_b.package_hash

    # Re-assembling the SAME package (idempotent re-entry) returns the same hash.
    result_a_again = assemble_contest_package(db, fx_a["case"].case_id, fx_a["review_action"].id, current_time=FIXED_NOW)
    assert result_a_again.package_hash == result_a.package_hash
    assert result_a_again.contest_package_id == result_a.contest_package_id


# 11. Deterministic idempotency keys
def test_deterministic_idempotency_keys(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)

    from app.services.external_action.outbox_writer import _idempotency_key_upload, _idempotency_key_contest
    doc_id = fx["documents"][0].document_id
    expected_upload_key = _idempotency_key_upload(assembly.contest_package_id, doc_id)
    expected_contest_key = _idempotency_key_contest(assembly.contest_package_id, "submit")

    upload_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.UPLOAD_DOCUMENT).first()
    contest_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()
    assert upload_row.idempotency_key == expected_upload_key
    assert contest_row.idempotency_key == expected_contest_key


# 12. Duplicate outbox creation blocked
def test_duplicate_outbox_creation_blocked(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)

    result1 = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert len(result1.created_outbox_ids) == 2  # 1 upload + 1 contest
    assert len(result1.skipped_existing_outbox_ids) == 0

    result2 = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result2.written is True
    assert len(result2.created_outbox_ids) == 0
    assert len(result2.skipped_existing_outbox_ids) == 2

    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.case_id == fx["case"].case_id).count() == 2


# 13. Multiple document uploads produce separate logical upload actions
def test_multiple_documents_produce_separate_upload_actions(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(
        db, merchant, n_documents=2,
        document_evidence_types=[EvidenceType.PROOF_OF_DELIVERY, EvidenceType.CUSTOMER_COMMUNICATION],
    )
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    upload_rows = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.UPLOAD_DOCUMENT).all()
    assert len(upload_rows) == 2
    assert {r.aggregate_id for r in upload_rows} == {d.document_id for d in fx["documents"]}
    assert len({r.idempotency_key for r in upload_rows}) == 2  # distinct keys


# 14 & 15. draft vs submit produce the correct logical outbox action type
def test_submit_action_outbox_type(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_DRAFT).count() == 0
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).count() == 1


def test_draft_action_outbox_type_when_queue_pending(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant)
    # Force H-10 to resolve to "draft" by making the queue item non-DONE,
    # while still exercising write_outbox_for_package's own H-10 read
    # (assembly already happened against the DONE state).
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    fx["queue_item"].queue_status = QueueStatus.PENDING
    db.commit()
    result = write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert result.written is True
    assert result.gate_summary.h10_action == "draft"
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_DRAFT).count() == 1
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).count() == 0


# 16. Dispatch claim behavior
def test_dispatch_claim_behavior(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    outbox_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()
    assert outbox_row.status == OutboxStatus.PENDING

    result = dispatch_external_action_outbox(db, outbox_row.id, current_time=FIXED_NOW)
    assert result.dispatched is True
    db.refresh(outbox_row)
    assert outbox_row.status == OutboxStatus.FAILED  # boundary_not_implemented is terminal
    assert outbox_row.attempt_count == 1


def test_dispatch_claim_rejects_non_claimable_row(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    outbox_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()
    outbox_row.status = OutboxStatus.SENT
    db.commit()

    result = dispatch_external_action_outbox(db, outbox_row.id, current_time=FIXED_NOW)
    assert result.dispatched is False
    assert result.outcome == "not_claimable"


# 17. ExternalActionAttempt recording
def test_external_action_attempt_recorded(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    outbox_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    dispatch_external_action_outbox(db, outbox_row.id, current_time=FIXED_NOW)

    attempts = db.query(ExternalActionAttempt).filter(ExternalActionAttempt.outbox_id == outbox_row.id).all()
    assert len(attempts) == 1
    assert attempts[0].attempt_no == 1
    assert attempts[0].error_code == "EXTERNAL_BOUNDARY_NOT_IMPLEMENTED"
    assert attempts[0].completed_at is not None
    # No credentials/auth headers anywhere in the recorded metadata.
    assert "auth" not in str(attempts[0].request_metadata).lower()
    assert "key" not in str(attempts[0].request_metadata).lower() or "document" in str(attempts[0].request_metadata).lower()


# 18. Bounded retry/backoff scaffolding
def test_bounded_retry_backoff_scaffolding(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    outbox_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    def flaky_transport(_outbox):
        raise SimulatedTransientTransportFailure("simulated connection error")

    t = FIXED_NOW
    for expected_attempt in range(1, MAX_ATTEMPTS + 1):
        db.refresh(outbox_row)
        outbox_row.status = OutboxStatus.PENDING if expected_attempt == 1 else outbox_row.status
        if expected_attempt > 1:
            outbox_row.next_attempt_at = t
        db.commit()
        result = dispatch_external_action_outbox(db, outbox_row.id, transport=flaky_transport, current_time=t)
        assert result.outcome == "simulated_transient_failure"
        db.refresh(outbox_row)
        assert outbox_row.attempt_count == expected_attempt
        t = t + timedelta(hours=1)

    # After MAX_ATTEMPTS, the row is terminal and no longer claimable even
    # when its next_attempt_at has passed.
    outbox_row.next_attempt_at = t
    db.commit()
    result = dispatch_external_action_outbox(db, outbox_row.id, transport=flaky_transport, current_time=t)
    assert result.outcome == "not_claimable"


# 19. Local terminal failure behavior (unimplemented boundary)
def test_local_terminal_failure_boundary_not_implemented(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    outbox_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(db, outbox_row.id, current_time=FIXED_NOW)
    assert result.outcome == "boundary_not_implemented"
    db.refresh(outbox_row)
    assert outbox_row.status == OutboxStatus.FAILED
    assert outbox_row.next_attempt_at is None  # not scheduled for retry


# 20. UNKNOWN_RESULT handling
def test_unknown_result_not_auto_retried(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    outbox_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    def timeout_transport(_outbox):
        raise SimulatedUnknownTransportResult("simulated timeout, no response received")

    result = dispatch_external_action_outbox(db, outbox_row.id, transport=timeout_transport, current_time=FIXED_NOW)
    assert result.outcome == "unknown_result"
    db.refresh(outbox_row)
    assert outbox_row.status == OutboxStatus.FAILED
    assert outbox_row.next_attempt_at is None  # never auto-scheduled for retry
    attempt = db.query(ExternalActionAttempt).filter(ExternalActionAttempt.outbox_id == outbox_row.id).first()
    assert attempt.error_code == "UNKNOWN_RESULT"


# 21. Proof that no real HTTP client/network call is made
def test_no_network_capability_in_dispatch_module():
    import inspect
    import app.worker.external_action_tasks as dispatch_module
    import app.services.external_action.outbox_writer as writer_module
    import app.services.external_action.contest_package_assembly as assembly_module

    for module in (dispatch_module, writer_module, assembly_module):
        source = inspect.getsource(module).lower()
        for forbidden in ("import requests", "import httpx", "httpx.client", "httpx.get", "httpx.post", "urllib.request", "razorpay_api_key", "authorization:"):
            assert forbidden not in source, f"unexpected outbound-call marker '{forbidden}' found in {module.__name__}"


def test_default_transport_performs_no_io():
    from app.worker.external_action_tasks import _default_transport
    import inspect
    source = inspect.getsource(_default_transport)
    assert "raise ExternalBoundaryNotImplemented" in source
    with pytest.raises(ExternalBoundaryNotImplemented):
        _default_transport(None)


# 22. Tenant isolation
def test_tenant_isolation_assembly_and_outbox(db):
    merchant_a = make_merchant(db, name="Merchant J Tenant A")
    merchant_b = make_merchant(db, name="Merchant J Tenant B")
    fx_a = make_finalized_case(db, merchant_a)
    fx_b = make_finalized_case(db, merchant_b)

    # A case's review_action_id does not resolve under a different case_id.
    cross = assemble_contest_package(db, fx_b["case"].case_id, fx_a["review_action"].id, current_time=FIXED_NOW)
    assert cross.assembled is False

    assembly_a = assemble_contest_package(db, fx_a["case"].case_id, fx_a["review_action"].id, current_time=FIXED_NOW)
    cross_write = write_outbox_for_package(db, fx_b["case"].case_id, assembly_a.contest_package_id, current_time=FIXED_NOW)
    assert cross_write.written is False
    assert cross_write.reason == "ContestPackage not found for this case"


# 23. No ACCEPT action is generated
def test_no_accept_action_ever_generated(db):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    assert db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.ACCEPT).count() == 0

    import inspect
    import app.services.external_action.outbox_writer as writer_module
    source = inspect.getsource(writer_module)
    assert "ExternalActionType.ACCEPT" not in source
