import inspect
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.shared import Merchant, Case
from app.models.module_c import EvidenceDocument, EvidenceType, ScanStatus, EvidenceProcessingStatus
from app.services.external_action.evidence_mapping_gate import (
    map_evidence_type_to_razorpay_field,
    is_document_safe_for_contest,
    evaluate_evidence_for_contest,
    EvidenceGateErrorCode,
    EVIDENCE_TYPE_TO_RAZORPAY_FIELD,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_08_pg"
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


def make_case_with_document(
    db: Session,
    evidence_type=EvidenceType.PROOF_OF_DELIVERY,
    scan_status=ScanStatus.CLEAN,
    processing_status=EvidenceProcessingStatus.READY_FOR_OCR,
):
    merchant = Merchant(name="Test Merchant H08", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    document = EvidenceDocument(
        case_id=case.case_id,
        merchant_id=merchant.merchant_id,
        evidence_type=evidence_type.value,
        object_key=f"test/{uuid.uuid4()}",
        mime_type="application/pdf",
        file_size_bytes=100,
        sha256=uuid.uuid4().hex,
        scan_status=scan_status,
        processing_status=processing_status,
    )
    db.add(document)
    db.commit()
    return case, document


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# --- Mapping ---------------------------------------------------------------

@pytest.mark.parametrize("evidence_type,expected_field", sorted(EVIDENCE_TYPE_TO_RAZORPAY_FIELD.items(), key=lambda kv: kv[0].value))
def test_each_mapped_evidence_type(evidence_type, expected_field):
    result = map_evidence_type_to_razorpay_field(evidence_type)
    assert result.mapped is True
    assert result.error_code is None
    assert result.razorpay_evidence_field == expected_field


@pytest.mark.parametrize("evidence_type", [EvidenceType.INVOICE, EvidenceType.COURIER_TRACKING, EvidenceType.OTHER])
def test_unmapped_evidence_types(evidence_type):
    result = map_evidence_type_to_razorpay_field(evidence_type)
    assert result.mapped is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_MAPPING_INVALID
    assert result.razorpay_evidence_field is None


# --- Safety ------------------------------------------------------------------

def test_clean_document_is_safe(db):
    case, document = make_case_with_document(db, scan_status=ScanStatus.CLEAN)
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is True
    assert result.error_code is None


def test_infected_document_is_unsafe(db):
    case, document = make_case_with_document(db, scan_status=ScanStatus.INFECTED, processing_status=EvidenceProcessingStatus.REJECTED)
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE


def test_failed_scan_document_is_unsafe(db):
    case, document = make_case_with_document(db, scan_status=ScanStatus.FAILED, processing_status=EvidenceProcessingStatus.SCAN_FAILED)
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE


def test_pending_scan_document_is_unsafe(db):
    case, document = make_case_with_document(db, scan_status=ScanStatus.PENDING, processing_status=EvidenceProcessingStatus.QUARANTINED)
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE


# --- Scoping -----------------------------------------------------------------

def test_document_belonging_to_requested_case_is_evaluated(db):
    case, document = make_case_with_document(db, scan_status=ScanStatus.CLEAN)
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is True
    assert result.case_id == case.case_id
    assert result.document_id == document.document_id


def test_document_belonging_to_another_case_fails_closed(db):
    case1, document1 = make_case_with_document(db, scan_status=ScanStatus.CLEAN)
    case2, document2 = make_case_with_document(db, scan_status=ScanStatus.CLEAN)

    # document1 is CLEAN and safe under its own case, but must fail closed
    # when evaluated under case2's id rather than being silently accepted.
    result = is_document_safe_for_contest(db, case2.case_id, document1.document_id, current_time=FIXED_NOW)
    assert result.safe is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE
    assert result.scan_status is None  # never evaluated, not just rejected after the fact


def test_missing_document_fails_closed(db):
    merchant = Merchant(name="Test Merchant H08 No Doc", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.commit()

    result = is_document_safe_for_contest(db, case.case_id, uuid.uuid4(), current_time=FIXED_NOW)
    assert result.safe is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE


# --- Freshness -----------------------------------------------------------------

def test_fresh_read_bypasses_stale_identity_map(db, alembic_engine):
    case, document = make_case_with_document(db, scan_status=ScanStatus.CLEAN)

    # Load into this session's identity map first, as ordinary app code might.
    loaded = db.query(EvidenceDocument).filter(EvidenceDocument.document_id == document.document_id).first()
    assert loaded.scan_status == ScanStatus.CLEAN

    # Mutate through a completely separate connection/transaction, bypassing
    # this session's identity map entirely (mirrors a concurrent scanner worker).
    import sqlalchemy as sa
    with alembic_engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE evidence_documents SET scan_status = :status WHERE document_id = :doc_id"),
            {"status": "INFECTED", "doc_id": str(document.document_id)},
        )
        conn.commit()

    # The stale in-memory object still (incorrectly) looks unchanged...
    assert loaded.scan_status == ScanStatus.CLEAN

    # ...but the gate must see the fresh value, not the stale cached one.
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE
    assert result.scan_status == "INFECTED"


# --- No external calls ---------------------------------------------------------

def test_no_external_action_capability_in_module():
    import app.services.external_action.evidence_mapping_gate as gate_module
    source = inspect.getsource(gate_module).lower()
    for forbidden in ("import requests", "import httpx", "import urllib", ".get(\"http", ".post(\"http"):
        assert forbidden not in source, f"unexpected outbound-call marker '{forbidden}' found in evidence_mapping_gate.py"


# --- Error code exactness -------------------------------------------------------

def test_error_code_exact_strings():
    assert EvidenceGateErrorCode.EVIDENCE_MAPPING_INVALID == "EVIDENCE_MAPPING_INVALID"
    assert EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE == "EVIDENCE_DOCUMENT_UNSAFE"


# --- ContestPackageDocument.approved is never used ------------------------------

def test_approved_field_never_referenced():
    # The module's docstring intentionally *mentions* ContestPackageDocument.approved
    # to document that it is deliberately not used (frozen decision) — what must NOT
    # be present is any actual import or code-level usage of it.
    import app.services.external_action.evidence_mapping_gate as gate_module
    assert not hasattr(gate_module, "ContestPackageDocument")
    source = inspect.getsource(gate_module)
    assert "import ContestPackageDocument" not in source
    assert "from app.models.module_h" not in source


# --- OCR independence ------------------------------------------------------------

def test_clean_with_ocr_failed_remains_safe(db):
    case, document = make_case_with_document(
        db, scan_status=ScanStatus.CLEAN, processing_status=EvidenceProcessingStatus.OCR_FAILED
    )
    result = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert result.safe is True
    assert result.error_code is None


# --- Determinism -------------------------------------------------------------------

def test_deterministic_behavior(db):
    case, document = make_case_with_document(db, scan_status=ScanStatus.CLEAN)
    r1 = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    r2 = is_document_safe_for_contest(db, case.case_id, document.document_id, current_time=FIXED_NOW)
    assert r1.safe == r2.safe == True
    assert r1.checked_at == r2.checked_at == FIXED_NOW


# --- Combined convenience function -------------------------------------------------

def test_combined_mapped_and_safe_is_eligible(db):
    case, document = make_case_with_document(db, evidence_type=EvidenceType.PROOF_OF_DELIVERY, scan_status=ScanStatus.CLEAN)
    result = evaluate_evidence_for_contest(db, case.case_id, document.document_id, EvidenceType.PROOF_OF_DELIVERY, current_time=FIXED_NOW)
    assert result.eligible is True
    assert result.error_code is None
    assert result.mapping.razorpay_evidence_field == "shipping_proof"
    assert result.safety.safe is True


def test_combined_unsafe_and_unmapped_reports_unsafe_first(db):
    # Frozen ordering: safety is evaluated first. A document that is both
    # unsafe AND has an unmapped evidence type must surface
    # EVIDENCE_DOCUMENT_UNSAFE, not EVIDENCE_MAPPING_INVALID.
    case, document = make_case_with_document(db, evidence_type=EvidenceType.INVOICE, scan_status=ScanStatus.INFECTED, processing_status=EvidenceProcessingStatus.REJECTED)
    result = evaluate_evidence_for_contest(db, case.case_id, document.document_id, EvidenceType.INVOICE, current_time=FIXED_NOW)
    assert result.eligible is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_DOCUMENT_UNSAFE
    assert result.safety.safe is False
    assert result.mapping.mapped is False  # computed, but not what determined the error_code


def test_combined_safe_but_unmapped_reports_mapping_invalid(db):
    case, document = make_case_with_document(db, evidence_type=EvidenceType.OTHER, scan_status=ScanStatus.CLEAN)
    result = evaluate_evidence_for_contest(db, case.case_id, document.document_id, EvidenceType.OTHER, current_time=FIXED_NOW)
    assert result.eligible is False
    assert result.error_code == EvidenceGateErrorCode.EVIDENCE_MAPPING_INVALID
    assert result.safety.safe is True
    assert result.mapping.mapped is False
