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
    ContestPackage, ContestPackageDocument, RazorpayDocumentLink,
)

from app.services.external_action.contest_package_assembly import assemble_contest_package
from app.services.external_action.razorpay_request_builder import (
    RazorpayDocumentLinkMissing,
    build_contest_request,
    build_upload_document_request,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_j_cat_d_rb_pg"
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
    merchant = Merchant(name="Test Merchant J-CatD-RB", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
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


def make_evidence_document(db: Session, case, merchant, evidence_type=EvidenceType.PROOF_OF_DELIVERY):
    doc = EvidenceDocument(
        case_id=case.case_id,
        merchant_id=merchant.merchant_id,
        evidence_type=evidence_type.value,
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


def make_case_with_package_documents(db: Session, merchant, evidence_types, contest_amount_minor=5000, dispute_amount_minor=10000):
    """
    Builds a finalized (DONE + APPROVE_CONTEST) case, assembles its
    ContestPackage via the real assembly pipeline, and returns
    (case, dispute, package, documents) — one document per entry in
    evidence_types, each already an approved ContestPackageDocument.
    """
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id, external_dispute_id=case.external_dispute_id,
        payment_id=f"pay_{uuid.uuid4()}", amount_minor=dispute_amount_minor, currency="INR",
        reason_code="fraud", status="open", dispute_created_at=datetime.now(timezone.utc),
        respond_by=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(dispute)
    db.flush()

    prediction = make_prediction(db, case)

    queue_item = ReviewQueueItem(
        case_id=case.case_id, prediction_id=prediction.id, priority_score=100,
        queue_status=QueueStatus.DONE, respond_by=dispute.respond_by,
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

    make_draft(db, case, contest_amount_minor=contest_amount_minor)
    documents = [make_evidence_document(db, case, merchant, evidence_type=et) for et in evidence_types]

    db.commit()

    assembly = assemble_contest_package(db, case.case_id, review_action.id, current_time=FIXED_NOW)
    package = db.query(ContestPackage).filter(ContestPackage.id == assembly.contest_package_id).first()
    return case, dispute, package, documents


def make_document_link(db: Session, document, razorpay_document_id):
    link = RazorpayDocumentLink(
        document_id=document.document_id,
        razorpay_document_id=razorpay_document_id,
        purpose="dispute_evidence",
        mime_type="application/pdf",
        size_bytes=1000,
        external_response_json={"id": razorpay_document_id},
    )
    db.add(link)
    db.flush()
    return link


# 1. Pure, no-DB function
def test_build_upload_document_request_purpose():
    assert build_upload_document_request() == {"purpose": "dispute_evidence"}


# 2. Single document, single field
def test_build_contest_request_single_document(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(db, merchant, [EvidenceType.PROOF_OF_DELIVERY])
    make_document_link(db, documents[0], "doc_abc123")
    db.commit()

    built = build_contest_request(db, case.case_id, package, "submit")
    assert built.razorpay_dispute_id == dispute.external_dispute_id
    assert built.body["action"] == "submit"
    assert built.body["amount"] == package.contest_amount_minor
    assert built.body["summary"] == package.summary
    assert built.body["evidence"] == {"shipping_proof": ["doc_abc123"]}


# 3. Multiple documents under the same field are grouped into an array
def test_build_contest_request_groups_multiple_documents_same_field(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(
        db, merchant, [EvidenceType.PROOF_OF_DELIVERY, EvidenceType.PROOF_OF_DELIVERY],
    )
    make_document_link(db, documents[0], "doc_aaa")
    make_document_link(db, documents[1], "doc_bbb")
    db.commit()

    built = build_contest_request(db, case.case_id, package, "submit")
    assert built.body["evidence"] == {"shipping_proof": ["doc_aaa", "doc_bbb"]}


# 4. Documents across different fields produce separate arrays
def test_build_contest_request_groups_multiple_fields(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(
        db, merchant, [EvidenceType.PROOF_OF_DELIVERY, EvidenceType.CUSTOMER_COMMUNICATION],
    )
    make_document_link(db, documents[0], "doc_aaa")
    make_document_link(db, documents[1], "doc_bbb")
    db.commit()

    built = build_contest_request(db, case.case_id, package, "submit")
    assert built.body["evidence"] == {"shipping_proof": ["doc_aaa"], "customer_communication": ["doc_bbb"]}


# 5. A missing RazorpayDocumentLink is a local, non-network failure
def test_build_contest_request_raises_when_link_missing(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(db, merchant, [EvidenceType.PROOF_OF_DELIVERY])
    # No RazorpayDocumentLink created for the document.

    with pytest.raises(RazorpayDocumentLinkMissing) as excinfo:
        build_contest_request(db, case.case_id, package, "submit")
    assert excinfo.value.document_id == documents[0].document_id


# 6. Unapproved documents are excluded; only H-08's frozen fields ever appear
def test_build_contest_request_only_uses_approved_documents(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(
        db, merchant, [EvidenceType.PROOF_OF_DELIVERY, EvidenceType.CUSTOMER_COMMUNICATION],
    )
    make_document_link(db, documents[0], "doc_aaa")
    make_document_link(db, documents[1], "doc_bbb")
    pkg_doc_2 = (
        db.query(ContestPackageDocument)
        .filter(ContestPackageDocument.contest_package_id == package.id, ContestPackageDocument.document_id == documents[1].document_id)
        .first()
    )
    pkg_doc_2.approved = False
    db.commit()

    built = build_contest_request(db, case.case_id, package, "submit")
    assert built.body["evidence"] == {"shipping_proof": ["doc_aaa"]}

    ALLOWED_FIELDS = {"shipping_proof", "customer_communication", "proof_of_service", "term_and_conditions", "refund_confirmation"}
    assert set(built.body["evidence"].keys()).issubset(ALLOWED_FIELDS)


# 7. Missing dispute raises a plain, local ValueError
def test_build_contest_request_raises_when_dispute_missing(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(db, merchant, [EvidenceType.PROOF_OF_DELIVERY])
    db.query(Dispute).filter(Dispute.case_id == case.case_id).delete()
    db.commit()

    with pytest.raises(ValueError):
        build_contest_request(db, case.case_id, package, "draft")


# 8. Draft action is carried through verbatim into the request body
def test_build_contest_request_draft_action(db):
    merchant = make_merchant(db)
    case, dispute, package, documents = make_case_with_package_documents(db, merchant, [])

    built = build_contest_request(db, case.case_id, package, "draft")
    assert built.body["action"] == "draft"
    assert built.body["evidence"] == {}
