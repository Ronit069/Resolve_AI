"""
Module J, Category D — dispatch worker tests.

Exercises make_razorpay_production_transport + dispatch_external_action_outbox
together, using httpx.MockTransport injected into a RazorpayClient (zero
real network calls) so the full real-transport retry/error-classification
wiring is verified end to end: success (SENT + domain writes), the only
retryable condition (429), and every terminal condition (4xx validation,
401/403 auth, 5xx/malformed/timeout -> UNKNOWN_RESULT, live-mode guard,
missing RazorpayDocumentLink).

dispatch_external_action_outbox's own DEFAULT transport is untouched by
Category D (see test_module_j_category_b.py) — every test here passes an
explicit `transport=` built from make_razorpay_production_transport.
"""

import uuid
import itertools
import hashlib
from datetime import datetime, timezone, timedelta

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.core.storage import storage_client
from app.models.shared import Merchant, Case, AppUser, AppUserRole
from app.models.module_a import Dispute
from app.models.module_c import EvidenceDocument, EvidenceType, ScanStatus, EvidenceProcessingStatus
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion, EValidationRunStatus
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.models.module_g import ResponseGenerationRun, GeneratedDraft, GenerationStatus, GuardrailStatus
from app.models.module_h import (
    ReviewQueueItem, ReviewAction, QueueStatus, ReviewActionEnum,
    ContestPackage, ContestPackageDocument, ContestPackageStatus,
    ContestSubmission, SubmissionStatus,
    ExternalActionOutbox, ExternalActionType, OutboxStatus,
    RazorpayDocumentLink,
)

from app.services.external_action.contest_package_assembly import assemble_contest_package
from app.services.external_action.outbox_writer import write_outbox_for_package
from app.services.external_action.razorpay_client import RAZORPAY_BASE_URL, RazorpayClient
from app.worker.external_action_tasks import (
    dispatch_external_action_outbox,
    make_razorpay_production_transport,
    SimulatedTransientTransportFailure,
    MAX_ATTEMPTS,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_j_cat_d_dispatch_pg"
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
    merchant = Merchant(name="Test Merchant J-CatD-Dispatch", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    return merchant


def make_prediction(db: Session, case):
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
        recommendation="CONTEST",
        hard_block=False,
        idempotency_key=f"pred_{uuid.uuid4()}",
    )
    db.add(prediction)
    db.flush()
    return prediction


def make_evidence_document(db: Session, case, merchant):
    doc = EvidenceDocument(
        case_id=case.case_id,
        merchant_id=merchant.merchant_id,
        evidence_type=EvidenceType.PROOF_OF_DELIVERY.value,
        object_key=f"key_{uuid.uuid4()}",
        original_filename="proof.pdf",
        mime_type="application/pdf",
        file_size_bytes=1000,
        sha256=hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
        scan_status=ScanStatus.CLEAN,
        processing_status=EvidenceProcessingStatus.READY_FOR_OCR,
    )
    db.add(doc)
    db.flush()
    return doc


def make_draft(db: Session, case, contest_amount_minor=5000, summary="Evidence supports contest."):
    run = ResponseGenerationRun(
        case_id=case.case_id, prompt_template_version="v1", llm_model_version="test-model",
        guardrail_version="v1", status=GenerationStatus.PASS,
    )
    db.add(run)
    db.flush()
    draft = GeneratedDraft(
        generation_run_id=run.id, case_id=case.case_id, summary=summary,
        contest_amount_minor=contest_amount_minor, draft_json={"summary": summary},
        guardrail_status=GuardrailStatus.PASS, is_current=True,
    )
    db.add(draft)
    db.flush()
    return draft


def make_finalized_case(db: Session, merchant, n_documents=1, queue_status=QueueStatus.DONE):
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id, external_dispute_id=case.external_dispute_id,
        payment_id=f"pay_{uuid.uuid4()}", amount_minor=10000, currency="INR",
        reason_code="fraud", status="open", dispute_created_at=datetime.now(timezone.utc),
        respond_by=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(dispute)
    db.flush()

    prediction = make_prediction(db, case)

    queue_item = ReviewQueueItem(
        case_id=case.case_id, prediction_id=prediction.id, priority_score=100,
        queue_status=queue_status, respond_by=dispute.respond_by,
    )
    db.add(queue_item)
    db.flush()

    reviewer = AppUser(merchant_id=merchant.merchant_id, email=f"reviewer_{uuid.uuid4()}@test.com", is_active=True, role=AppUserRole.APPROVER)
    db.add(reviewer)
    db.flush()

    review_action = ReviewAction(
        queue_item_id=queue_item.id, case_id=case.case_id, reviewer_id=reviewer.user_id,
        action=ReviewActionEnum.APPROVE_CONTEST,
    )
    db.add(review_action)
    db.flush()

    draft = make_draft(db, case)
    documents = [make_evidence_document(db, case, merchant) for _ in range(n_documents)]

    db.commit()
    return {"case": case, "dispute": dispute, "queue_item": queue_item, "review_action": review_action, "draft": draft, "documents": documents}


def _mock_client(monkeypatch, handler, key_id="rzp_test_abc123", secret="secret_xyz", allow_live=False):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", key_id)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", secret)
    monkeypatch.setattr(settings, "RAZORPAY_ALLOW_LIVE_MODE", allow_live)
    client = RazorpayClient()
    client._client = httpx.Client(base_url=RAZORPAY_BASE_URL, auth=(key_id, secret), transport=httpx.MockTransport(handler))
    return client


def _setup_submitted_case(db, merchant, n_documents=1):
    fx = make_finalized_case(db, merchant, n_documents=n_documents)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    return fx, assembly


# 1. UPLOAD_DOCUMENT success writes a RazorpayDocumentLink and sets SENT
def test_upload_document_success(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    monkeypatch.setattr(storage_client, "download_file", lambda object_name: b"filebytes")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/documents"
        return httpx.Response(200, json={"id": "doc_razorpay_1", "purpose": "dispute_evidence"})

    mock_client = _mock_client(monkeypatch, handler)
    upload_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.UPLOAD_DOCUMENT).first()

    result = dispatch_external_action_outbox(
        db, upload_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "sent"
    db.refresh(upload_row)
    assert upload_row.status == OutboxStatus.SENT

    link = db.query(RazorpayDocumentLink).filter(RazorpayDocumentLink.document_id == upload_row.aggregate_id).first()
    assert link is not None
    assert link.razorpay_document_id == "doc_razorpay_1"


# 2. CONTEST_SUBMIT success writes a ContestSubmission and sets package SUBMITTED
def test_contest_submit_success(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_already_uploaded",
        purpose="dispute_evidence", mime_type="application/pdf", size_bytes=1000,
        external_response_json={"id": "doc_already_uploaded"},
    ))
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/contest" in request.url.path
        return httpx.Response(200, json={"id": fx["dispute"].external_dispute_id, "status": "under_review", "evidence": {"shipping_proof": ["doc_already_uploaded"]}})

    mock_client = _mock_client(monkeypatch, handler)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "sent"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.SENT

    submission = db.query(ContestSubmission).filter(ContestSubmission.contest_package_id == assembly.contest_package_id).first()
    assert submission is not None
    assert submission.status == SubmissionStatus.SUCCESS
    assert submission.action == "submit"
    assert submission.submitted_at is not None
    assert submission.external_dispute_id == fx["dispute"].external_dispute_id

    package = db.query(ContestPackage).filter(ContestPackage.id == assembly.contest_package_id).first()
    assert package.status == ContestPackageStatus.SUBMITTED


# 3. CONTEST_DRAFT success does not flip package to SUBMITTED
def test_contest_draft_success_leaves_package_approved(db, monkeypatch):
    merchant = make_merchant(db)
    fx = make_finalized_case(db, merchant, n_documents=1, queue_status=QueueStatus.PENDING)
    assembly = assemble_contest_package(db, fx["case"].case_id, fx["review_action"].id, current_time=FIXED_NOW)
    write_outbox_for_package(db, fx["case"].case_id, assembly.contest_package_id, current_time=FIXED_NOW)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_already_uploaded",
        purpose="dispute_evidence", mime_type="application/pdf", size_bytes=1000,
        external_response_json={"id": "doc_already_uploaded"},
    ))
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": fx["dispute"].external_dispute_id, "status": "draft", "evidence": {}})

    mock_client = _mock_client(monkeypatch, handler)
    draft_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_DRAFT).first()

    result = dispatch_external_action_outbox(
        db, draft_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "sent"

    submission = db.query(ContestSubmission).filter(ContestSubmission.contest_package_id == assembly.contest_package_id).first()
    assert submission.action == "draft"
    assert submission.submitted_at is None

    package = db.query(ContestPackage).filter(ContestPackage.id == assembly.contest_package_id).first()
    assert package.status == ContestPackageStatus.APPROVED  # not SUBMITTED


# 4. Missing RazorpayDocumentLink at submit time is a terminal, non-retried failure
def test_submit_preflight_missing_document_link_is_terminal(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    # Deliberately do NOT create a RazorpayDocumentLink for the document.

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call should be attempted when a document link is missing")

    mock_client = _mock_client(monkeypatch, handler)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "razorpay_document_link_missing"
    assert result.error_code == "RAZORPAY_DOCUMENT_LINK_MISSING"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.FAILED
    assert submit_row.next_attempt_at is None  # terminal, never retried


# 5. 429 is retryable with bounded exponential backoff
def test_rate_limited_is_retryable(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_x", purpose="dispute_evidence",
        mime_type="application/pdf", size_bytes=1000, external_response_json={"id": "doc_x"},
    ))
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "TOO_MANY_REQUESTS", "description": "slow down"}})

    mock_client = _mock_client(monkeypatch, handler)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "razorpay_rate_limited"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.FAILED
    assert submit_row.next_attempt_at is not None  # scheduled for retry
    assert submit_row.attempt_count == 1
    assert submit_row.attempt_count < MAX_ATTEMPTS


# 6. 4xx validation error is terminal
def test_validation_error_is_terminal(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_x", purpose="dispute_evidence",
        mime_type="application/pdf", size_bytes=1000, external_response_json={"id": "doc_x"},
    ))
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "amount invalid"}})

    mock_client = _mock_client(monkeypatch, handler)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "razorpay_validation_error"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.FAILED
    assert submit_row.next_attempt_at is None


# 7. 401/403 auth error is terminal
def test_auth_error_is_terminal(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_x", purpose="dispute_evidence",
        mime_type="application/pdf", size_bytes=1000, external_response_json={"id": "doc_x"},
    ))
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "description": "bad credentials"}})

    mock_client = _mock_client(monkeypatch, handler)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "razorpay_auth_error"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.FAILED
    assert submit_row.next_attempt_at is None


# 8. 5xx collapses into UNKNOWN_RESULT, terminal, never retried (frozen PO override)
def test_5xx_collapses_into_unknown_result_terminal(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_x", purpose="dispute_evidence",
        mime_type="application/pdf", size_bytes=1000, external_response_json={"id": "doc_x"},
    ))
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    mock_client = _mock_client(monkeypatch, handler)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db, client=mock_client), current_time=FIXED_NOW,
    )
    assert result.outcome == "unknown_result"
    assert result.error_code == "UNKNOWN_RESULT"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.FAILED
    assert submit_row.next_attempt_at is None  # never auto-retried, unlike a 429


# 9. Live-mode guard fires at dispatch time, terminal
def test_live_mode_guard_fires_at_dispatch_time(db, monkeypatch):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    doc = fx["documents"][0]
    db.add(RazorpayDocumentLink(
        document_id=doc.document_id, razorpay_document_id="doc_x", purpose="dispute_evidence",
        mime_type="application/pdf", size_bytes=1000, external_response_json={"id": "doc_x"},
    ))
    db.commit()

    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_live_realkey")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setattr(settings, "RAZORPAY_ALLOW_LIVE_MODE", False)

    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()
    result = dispatch_external_action_outbox(
        db, submit_row.id, transport=make_razorpay_production_transport(db), current_time=FIXED_NOW,
    )
    assert result.outcome == "razorpay_live_mode_not_allowed"
    db.refresh(submit_row)
    assert submit_row.status == OutboxStatus.FAILED
    assert submit_row.next_attempt_at is None


# 10. Category D wiring never changes dispatch_external_action_outbox's own default transport
def test_default_transport_unaffected_by_category_d(db):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    result = dispatch_external_action_outbox(db, submit_row.id, current_time=FIXED_NOW)
    assert result.outcome == "boundary_not_implemented"


# 11. Two-commit boundary: the with_for_update row lock is released before transport is called
def test_two_commit_boundary_releases_lock_before_transport(db, alembic_engine):
    merchant = make_merchant(db)
    fx, assembly = _setup_submitted_case(db, merchant, n_documents=1)
    submit_row = db.query(ExternalActionOutbox).filter(ExternalActionOutbox.action_type == ExternalActionType.CONTEST_SUBMIT).first()

    lock_probe = {}

    def probing_transport(_outbox):
        import sqlalchemy as sa
        try:
            with alembic_engine.connect() as conn:
                conn.execute(sa.text("SET lock_timeout = '200ms'"))
                conn.execute(
                    sa.text("SELECT id FROM external_action_outbox WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": str(submit_row.id)},
                )
                conn.rollback()
            lock_probe["acquired"] = True
        except Exception as exc:
            lock_probe["acquired"] = False
            lock_probe["error"] = str(exc)
        raise SimulatedTransientTransportFailure("probe complete")

    dispatch_external_action_outbox(db, submit_row.id, transport=probing_transport, current_time=FIXED_NOW)
    assert lock_probe.get("acquired") is True, f"row lock was still held during transport call: {lock_probe.get('error')}"
