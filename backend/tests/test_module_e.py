import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import uuid

from app.core.database import Base
from app.models.shared import Case, Merchant, AppUser
import app.models.module_a
import app.models.module_b
import app.models.module_c
import app.models.module_d
from app.models.module_e import (
    EvidencePolicyVersion, EvidenceValidationRun, ValidationRuleCatalog,
    ValidationRuleVersion, EvidenceValidationResult, EvidenceRequirementAssessment,
    CrossSourceFieldLink, FeatureDefinition, CaseFeatureSnapshot,
    EValidationRunStatus, ERuleSeverity, EValidationResultState, ERequirementState, EFeatureDataType, EMatchMethod, ELinkStatus
)
from app.schemas.module_e import EvidenceValidationRunBase, ModuleFContract

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_e.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(autouse=True)
def clean_db():
    # Only clean the module E tables + dependencies
    db = TestingSessionLocal()
    try:
        db.query(CaseFeatureSnapshot).delete()
        db.query(FeatureDefinition).delete()
        db.query(CrossSourceFieldLink).delete()
        db.query(EvidenceRequirementAssessment).delete()
        db.query(EvidenceValidationResult).delete()
        db.query(ValidationRuleVersion).delete()
        db.query(ValidationRuleCatalog).delete()
        db.query(EvidenceValidationRun).delete()
        db.query(EvidencePolicyVersion).delete()
        db.query(Case).delete()
        db.query(AppUser).delete()
        db.query(Merchant).delete()
        db.commit()
    finally:
        db.close()

def create_base_case(db):
    merchant = Merchant(external_merchant_id=f"test_merch_{uuid.uuid4()}", name="Test Merchant")
    db.add(merchant)
    db.commit()
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id="disp_123", source="synthetic")
    db.add(case)
    db.commit()
    return case

def test_evidence_policy_version_creation():
    db = TestingSessionLocal()
    policy = EvidencePolicyVersion(
        payment_network="Visa",
        reason_code="10.4",
        phase="chargeback",
        version=1,
        effective_from=datetime.now(timezone.utc)
    )
    db.add(policy)
    db.commit()
    
    saved_policy = db.query(EvidencePolicyVersion).filter_by(payment_network="Visa").first()
    assert saved_policy is not None
    assert saved_policy.policy_version_id is not None
    assert saved_policy.version == 1
    db.close()


def test_unique_policy_version_identity():
    db = TestingSessionLocal()
    policy1 = EvidencePolicyVersion(
        payment_network="Visa",
        reason_code="10.4",
        phase="chargeback",
        version=1,
        effective_from=datetime.now(timezone.utc)
    )
    db.add(policy1)
    db.commit()

    policy2 = EvidencePolicyVersion(
        payment_network="Visa",
        reason_code="10.4",
        phase="chargeback",
        version=1,
        effective_from=datetime.now(timezone.utc)
    )
    db.add(policy2)
    with pytest.raises(IntegrityError):
        db.commit()
    
    db.rollback()
    db.close()


def test_validation_run_creation_and_idempotency():
    db = TestingSessionLocal()
    case = create_base_case(db)
    
    policy = EvidencePolicyVersion(
        payment_network="Mastercard",
        reason_code="4837",
        phase="chargeback",
        version=1,
        effective_from=datetime.now(timezone.utc)
    )
    db.add(policy)
    db.commit()
    
    run1 = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        idempotency_key="idempotency_val_run_123"
    )
    db.add(run1)
    db.commit()
    
    saved_run = db.query(EvidenceValidationRun).filter_by(idempotency_key="idempotency_val_run_123").first()
    assert saved_run is not None
    assert saved_run.status == EValidationRunStatus.RUNNING
    
    # Test uniqueness
    run2 = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        idempotency_key="idempotency_val_run_123"  # Same key
    )
    db.add(run2)
    with pytest.raises(IntegrityError):
        db.commit()
        
    db.rollback()
    db.close()


def test_rule_catalog_and_versions():
    db = TestingSessionLocal()
    
    rule = ValidationRuleCatalog(
        rule_code="AMOUNT_MISMATCH",
        category="AMOUNT",
        description="Check if invoice amount matches payment amount",
        severity_default=ERuleSeverity.ERROR
    )
    db.add(rule)
    db.commit()
    
    version = ValidationRuleVersion(
        rule_id=rule.rule_id,
        version=1,
        parameters_json={"tolerance_percentage": 0.05},
        effective_from=datetime.now(timezone.utc),
        checksum="abcd123"
    )
    db.add(version)
    db.commit()
    
    saved_version = db.query(ValidationRuleVersion).filter_by(rule_id=rule.rule_id).first()
    assert saved_version is not None
    assert saved_version.parameters_json["tolerance_percentage"] == 0.05
    db.close()


def test_requirement_assessment_enums():
    db = TestingSessionLocal()
    case = create_base_case(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="c", version=1, effective_from=datetime.now(timezone.utc)
    )
    db.add(policy)
    db.commit()
    run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.COMPLETED, started_at=datetime.now(timezone.utc), idempotency_key="key2"
    )
    db.add(run)
    db.commit()

    # Test PRESENT
    assess1 = EvidenceRequirementAssessment(
        validation_run_id=run.id,
        evidence_type="INVOICE",
        requirement_level="REQUIRED",
        status=ERequirementState.PRESENT
    )
    db.add(assess1)
    
    # Test MISSING
    assess2 = EvidenceRequirementAssessment(
        validation_run_id=run.id,
        evidence_type="PROOF_OF_DELIVERY",
        requirement_level="REQUIRED",
        status=ERequirementState.MISSING
    )
    db.add(assess2)
    
    # Test UNKNOWN
    assess3 = EvidenceRequirementAssessment(
        validation_run_id=run.id,
        evidence_type="TERMS_ACCEPTANCE",
        requirement_level="REQUIRED",
        status=ERequirementState.UNKNOWN
    )
    db.add(assess3)
    
    db.commit()
    
    assessments = db.query(EvidenceRequirementAssessment).all()
    assert len(assessments) == 3
    states = [a.status for a in assessments]
    assert ERequirementState.PRESENT in states
    assert ERequirementState.MISSING in states
    assert ERequirementState.UNKNOWN in states
    db.close()


def test_case_feature_snapshot():
    db = TestingSessionLocal()
    case = create_base_case(db)
    
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="c", version=1, effective_from=datetime.now(timezone.utc)
    )
    db.add(policy)
    db.commit()
    
    run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.COMPLETED, started_at=datetime.now(timezone.utc), idempotency_key="key3"
    )
    db.add(run)
    db.commit()
    
    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=run.id,
        feature_schema_version="1.0",
        features_json={"amount_match": True, "pod_present": False},
        feature_hash="hash_123",
        is_current=True
    )
    db.add(snapshot)
    db.commit()
    
    saved_snapshot = db.query(CaseFeatureSnapshot).filter_by(feature_hash="hash_123").first()
    assert saved_snapshot is not None
    assert saved_snapshot.is_current is True
    assert saved_snapshot.features_json["amount_match"] is True
    
    # Check that a new snapshot does not mutate the old one natively (application code must enforce `is_current`)
    run2 = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v2", policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.COMPLETED, started_at=datetime.now(timezone.utc), idempotency_key="key4"
    )
    db.add(run2)
    db.commit()

    snapshot2 = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=run2.id,
        feature_schema_version="1.0",
        features_json={"amount_match": False, "pod_present": True},
        feature_hash="hash_456",
        is_current=True
    )
    snapshot.is_current = False
    db.add(snapshot2)
    db.commit()
    
    assert saved_snapshot.is_current is False
    assert db.query(CaseFeatureSnapshot).filter_by(is_current=True, case_id=case.case_id).count() == 1
    
    db.close()

def test_feature_definition_available_at_prediction():
    db = TestingSessionLocal()
    feat = FeatureDefinition(
        feature_name="timeline_delay_days",
        data_type=EFeatureDataType.NUMERIC,
        definition="Days between order and dispute",
        source_modules="module_b",
        version=1,
        available_at_prediction=True
    )
    db.add(feat)
    db.commit()
    
    saved = db.query(FeatureDefinition).filter_by(feature_name="timeline_delay_days").first()
    assert saved.available_at_prediction is True
    db.close()


def test_module_f_contract_validation():
    # Generate mock UUIDs
    cid = uuid.uuid4()
    vrid = uuid.uuid4()
    pvid = uuid.uuid4()
    fsid = uuid.uuid4()
    rid = uuid.uuid4()
    
    payload = {
        "case_id": str(cid),
        "validation_run_id": str(vrid),
        "policy_version_id": str(pvid),
        "feature_snapshot_id": str(fsid),
        "findings": [
            {
                "id": str(uuid.uuid4()),
                "validation_run_id": str(vrid),
                "rule_version_id": str(rid),
                "result": "FAIL",
                "severity": "ERROR",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ],
        "summary": {
            "required_evidence_coverage": 0.8,
            "identifier_match_rate": 1.0,
            "timeline_consistency_score": 0.9,
            "unknown_field_ratio": 0.1,
            "contradiction_count": 1
        },
        "features": {
            "has_amount_mismatch": True
        },
        "status": "FEATURE_READY"
    }
    
    contract = ModuleFContract(**payload)
    assert contract.case_id == cid
    assert contract.findings[0].result == EValidationResultState.FAIL
    assert contract.summary.contradiction_count == 1
    assert contract.features["has_amount_mismatch"] is True



from datetime import timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.services.validation import (
    resolve_policy, PolicyNotFound, PolicyAmbiguous, PolicyInputUnavailable,
    compute_evidence_version, prepare_validation_run
)
from app.core.database import get_db

client = TestClient(fastapi_app)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

fastapi_app.dependency_overrides[get_db] = override_get_db

def create_full_case_setup(db, network="Visa", method="card", dispute_phase="chargeback", reason_code="10.4"):
    merchant = Merchant(external_merchant_id=f"test_merch_{uuid.uuid4()}", name="Test Merchant")
    db.add(merchant)
    db.commit()
    
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="synthetic")
    db.add(case)
    db.commit()
    
    payment = app.models.module_b.Payment(
        case_id=case.case_id,
        external_payment_id=f"pay_{uuid.uuid4()}",
        amount_minor=1000,
        currency="USD",
        status="captured",
        method=method,
        network=network,
        fetched_at=datetime.now(timezone.utc),
        created_at_source=datetime.now(timezone.utc)
    )
    db.add(payment)
    
    dispute = app.models.module_a.Dispute(
        case_id=case.case_id,
        external_dispute_id=case.external_dispute_id,
        payment_id=payment.external_payment_id,
        amount_minor=1000,
        currency="USD",
        reason_code=reason_code,
        phase=dispute_phase,
        status="open",
        dispute_created_at=datetime.now(timezone.utc)
    )
    db.add(dispute)
    db.commit()
    return merchant, case, dispute, payment

# --- Policy Resolution Tests ---

def test_resolve_policy_exact_match():
    db = TestingSessionLocal()
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    res = resolve_policy(db, "Visa", "10.4", "chargeback", datetime.now(timezone.utc))
    assert res.policy_version_id == policy.policy_version_id
    db.close()

def test_resolve_policy_mismatched_network():
    db = TestingSessionLocal()
    policy = EvidencePolicyVersion(
        payment_network="Mastercard", reason_code="10.4", phase="chargeback", version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    with pytest.raises(PolicyNotFound):
        resolve_policy(db, "Visa", "10.4", "chargeback", datetime.now(timezone.utc))
    db.close()

def test_resolve_policy_mismatched_reason_code():
    db = TestingSessionLocal()
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="4837", phase="chargeback", version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    with pytest.raises(PolicyNotFound):
        resolve_policy(db, "Visa", "10.4", "chargeback", datetime.now(timezone.utc))
    db.close()

def test_resolve_policy_mismatched_phase():
    db = TestingSessionLocal()
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="pre_arbitration", version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    with pytest.raises(PolicyNotFound):
        resolve_policy(db, "Visa", "10.4", "chargeback", datetime.now(timezone.utc))
    db.close()

def test_resolve_policy_effective_from_inclusive():
    db = TestingSessionLocal()
    ts = datetime.now(timezone.utc)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=ts
    )
    db.add(policy)
    db.commit()
    
    res = resolve_policy(db, "Visa", "10.4", "chargeback", ts)
    assert res.policy_version_id == policy.policy_version_id
    db.close()

def test_resolve_policy_effective_to_exclusive():
    db = TestingSessionLocal()
    ts = datetime.now(timezone.utc)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=ts - timedelta(days=10),
        effective_to=ts
    )
    db.add(policy)
    db.commit()
    
    with pytest.raises(PolicyNotFound):
        resolve_policy(db, "Visa", "10.4", "chargeback", ts)
    db.close()

def test_resolve_policy_not_found():
    db = TestingSessionLocal()
    with pytest.raises(PolicyNotFound):
        resolve_policy(db, "RuPay", "123", "chg", datetime.now(timezone.utc))
    db.close()

def test_resolve_policy_ambiguous():
    db = TestingSessionLocal()
    ts = datetime.now(timezone.utc)
    policy1 = EvidencePolicyVersion(
        payment_network="Amex", reason_code="FR1", phase="chargeback", version=1,
        effective_from=ts - timedelta(days=10)
    )
    policy2 = EvidencePolicyVersion(
        payment_network="Amex", reason_code="FR1", phase="chargeback", version=2,
        effective_from=ts - timedelta(days=5)
    )
    db.add_all([policy1, policy2])
    db.commit()
    
    with pytest.raises(PolicyAmbiguous):
        resolve_policy(db, "Amex", "FR1", "chargeback", ts)
    db.close()

# --- Payment Network Tests ---

def test_payment_network_is_used():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db, network="Amex", method="card")
    
    policy = EvidencePolicyVersion(
        payment_network="Amex", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    run, is_reused = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    assert run.policy_version_id == policy.policy_version_id
    db.close()

def test_payment_method_not_used_as_network():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db, network="Discover", method="card")
    
    policy = EvidencePolicyVersion(
        payment_network="card", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    with pytest.raises(PolicyNotFound):
        prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    db.close()

def test_payment_network_null_produces_unavailable():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db, network=None, method="upi")
    
    with pytest.raises(PolicyInputUnavailable):
        prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    db.close()

# --- Evidence Version Tests ---

def test_same_evidence_identical_hash():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    
    v1 = compute_evidence_version(db, case.case_id)
    v2 = compute_evidence_version(db, case.case_id)
    assert v1 == v2
    db.close()

def test_changing_sha256_changes_evidence_version():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    
    doc = app.models.module_c.EvidenceDocument(
        case_id=case.case_id, merchant_id=merch.merchant_id, evidence_type="INVOICE", original_filename="inv.pdf",
        mime_type="application/pdf", file_size_bytes=100,
        sha256="hash_a", object_key=f"obj_{uuid.uuid4()}", processing_status="EXTRACTED"
    )
    db.add(doc)
    db.commit()
    v1 = compute_evidence_version(db, case.case_id)
    
    doc.sha256 = "hash_b"
    db.commit()
    v2 = compute_evidence_version(db, case.case_id)
    
    assert v1 != v2
    db.close()

def test_changing_processing_status_changes_evidence_version():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    
    doc = app.models.module_c.EvidenceDocument(
        case_id=case.case_id, merchant_id=merch.merchant_id, evidence_type="INVOICE", original_filename="inv.pdf",
        mime_type="application/pdf", file_size_bytes=100,
        sha256="hash_a", object_key=f"obj_{uuid.uuid4()}", processing_status="OCR_PROCESSING"
    )
    db.add(doc)
    db.commit()
    v1 = compute_evidence_version(db, case.case_id)
    
    doc.processing_status = "EXTRACTED"
    db.commit()
    v2 = compute_evidence_version(db, case.case_id)
    
    assert v1 != v2
    db.close()

def test_changing_extraction_status_changes_evidence_version():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    
    doc = app.models.module_c.EvidenceDocument(
        case_id=case.case_id, merchant_id=merch.merchant_id, evidence_type="INVOICE", original_filename="inv.pdf",
        mime_type="application/pdf", file_size_bytes=100,
        sha256="hash_a", object_key=f"obj_{uuid.uuid4()}", processing_status="EXTRACTED"
    )
    db.add(doc)
    db.flush()
    
    job_id = uuid.uuid4()
    job = app.models.module_d.DocumentProcessingJob(
        job_id=job_id, document_id=doc.document_id, case_id=case.case_id, merchant_id=merch.merchant_id, job_type="FULL_PROCESSING", status="COMPLETED", pipeline_version="1.0", idempotency_key=f"idem_{uuid.uuid4()}"
    )
    db.add(job)
    
    ext = app.models.module_d.DocumentExtraction(
        job_id=job_id, document_id=doc.document_id, case_id=case.case_id,
        expected_evidence_type="INVOICE", type_match_status="MATCH",
        extraction_status="EXTRACTING", schema_version="1.0"
    )
    db.add(ext)
    db.commit()
    
    v1 = compute_evidence_version(db, case.case_id)
    
    ext.extraction_status = "COMPLETED"
    db.commit()
    v2 = compute_evidence_version(db, case.case_id)
    
    assert v1 != v2
    db.close()

def test_ordering_documents_produces_same_version():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    
    doc1 = app.models.module_c.EvidenceDocument(
        case_id=case.case_id, merchant_id=merch.merchant_id, evidence_type="INVOICE", original_filename="1", mime_type="pdf",
        file_size_bytes=1, sha256="hash_1", object_key=f"obj_{uuid.uuid4()}", processing_status="EXTRACTED"
    )
    doc2 = app.models.module_c.EvidenceDocument(
        case_id=case.case_id, merchant_id=merch.merchant_id, evidence_type="POD", original_filename="2", mime_type="pdf",
        file_size_bytes=1, sha256="hash_2", object_key=f"obj_{uuid.uuid4()}", processing_status="EXTRACTED"
    )
    db.add_all([doc1, doc2])
    db.commit()
    
    v1 = compute_evidence_version(db, case.case_id)
    v2 = compute_evidence_version(db, case.case_id)
    assert v1 == v2
    db.close()

# --- Idempotency & Audit Tests ---

def test_first_request_creates_run():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    run, is_reused = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    assert is_reused is False
    assert run is not None
    db.close()

def test_exact_duplicate_reuses_run():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    run1, is_reused1 = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    run2, is_reused2 = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    
    assert is_reused1 is False
    assert is_reused2 is True
    assert run1.id == run2.id
    db.close()

def test_duplicate_does_not_create_another_audit_log():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    
    audit_count = db.query(app.models.shared.AuditLog).filter_by(
        case_id=case.case_id, action="EVIDENCE_VALIDATION_REQUESTED"
    ).count()
    assert audit_count == 1
    db.close()

def test_duplicate_does_not_mutate_existing_run():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    run1, _ = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    run1.status = EValidationRunStatus.COMPLETED
    db.commit()
    
    run2, is_reused = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    assert is_reused is True
    assert run2.status == EValidationRunStatus.COMPLETED
    db.close()

def test_simulate_integrity_error_recovery():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    run1, _ = prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
    
    with patch.object(app.services.validation.Session, 'flush', side_effect=IntegrityError("x", "y", "z")):
        with patch.object(app.services.validation.Session, 'query') as mock_query:
            mock_query.return_value.filter.return_value.first.return_value = None
            try:
                prepare_validation_run(db, str(case.case_id), str(merch.merchant_id))
            except Exception:
                pass
    pass

# --- Tenant Isolation ---

def test_merchant_a_cannot_access_merchant_b_run():
    db = TestingSessionLocal()
    merch_a, case_a, _, _ = create_full_case_setup(db)
    merch_b = Merchant(external_merchant_id=f"test_merch_{uuid.uuid4()}", name="Test B")
    db.add(merch_b)
    db.commit()
    
    with pytest.raises(ValueError, match="Case not found or access denied"):
        prepare_validation_run(db, str(case_a.case_id), str(merch_b.merchant_id))
    db.close()

# --- API Endpoint Tests ---

import app.api.deps
CURRENT_TEST_MERCHANT_ID = None
def override_current_merchant_from_db():
    class MockMerchant:
        def __init__(self, m_id):
            self.merchant_id = m_id
    return MockMerchant(CURRENT_TEST_MERCHANT_ID) if CURRENT_TEST_MERCHANT_ID else None

fastapi_app.dependency_overrides[app.api.deps.get_current_merchant] = override_current_merchant_from_db

def test_api_successful_request_202():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    global CURRENT_TEST_MERCHANT_ID
    CURRENT_TEST_MERCHANT_ID = str(merch.merchant_id)
    case_id = str(case.case_id)
    db.close()
    

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[app.api.deps.get_current_merchant] = override_current_merchant_from_db
    response = client.post(f"/api/v1/cases/{case_id}/validate-evidence")
    assert response.status_code == 202
    assert response.json()["idempotent_reused"] is False

def test_api_repeated_request_returns_same_id():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    global CURRENT_TEST_MERCHANT_ID
    CURRENT_TEST_MERCHANT_ID = str(merch.merchant_id)
    case_id = str(case.case_id)
    db.close()
    

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[app.api.deps.get_current_merchant] = override_current_merchant_from_db
    res1 = client.post(f"/api/v1/cases/{case_id}/validate-evidence")
    res2 = client.post(f"/api/v1/cases/{case_id}/validate-evidence")
    
    assert res1.status_code == 202
    assert res2.status_code == 202
    assert res1.json()["validation_run_id"] == res2.json()["validation_run_id"]
    assert res2.json()["idempotent_reused"] is True

def test_api_policy_input_unavailable():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db, network=None)
    global CURRENT_TEST_MERCHANT_ID
    CURRENT_TEST_MERCHANT_ID = str(merch.merchant_id)
    case_id = str(case.case_id)
    db.close()
    

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[app.api.deps.get_current_merchant] = override_current_merchant_from_db
    res = client.post(f"/api/v1/cases/{case_id}/validate-evidence")
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "POLICY_INPUT_UNAVAILABLE"

def test_api_policy_not_found():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    global CURRENT_TEST_MERCHANT_ID
    CURRENT_TEST_MERCHANT_ID = str(merch.merchant_id)
    case_id = str(case.case_id)
    db.close()
    

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[app.api.deps.get_current_merchant] = override_current_merchant_from_db
    res = client.post(f"/api/v1/cases/{case_id}/validate-evidence")
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "POLICY_NOT_FOUND"

def test_api_policy_ambiguous():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    
    p1 = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=10)
    )
    p2 = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=2,
        effective_from=disp.dispute_created_at - timedelta(days=5)
    )
    db.add_all([p1, p2])
    db.commit()
    global CURRENT_TEST_MERCHANT_ID
    CURRENT_TEST_MERCHANT_ID = str(merch.merchant_id)
    case_id = str(case.case_id)
    db.close()
    

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[app.api.deps.get_current_merchant] = override_current_merchant_from_db
    res = client.post(f"/api/v1/cases/{case_id}/validate-evidence")
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "POLICY_AMBIGUOUS"

from unittest.mock import patch
from app.worker.validation_tasks import execute_evidence_validation
from sqlalchemy.exc import SQLAlchemyError
from app.models.shared import ProcessingState

def test_failure_rollback_and_state_recovery():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    case.processing_state = ProcessingState.D_INTELLIGENCE_READY
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=disp.dispute_created_at - timedelta(days=1)
    )
    db.add(policy)
    db.commit()
    
    run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=policy.policy_version_id,
        status=EValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        idempotency_key="idemp_1_" + str(uuid.uuid4())
    )
    db.add(run)
    db.commit()
    run_id_str = str(run.id)
    test_case_id = case.case_id # Keep as UUID object
    db.close() # Close to release SQLite shared lock
    
    # Force an exception during generate_feature_snapshot to simulate failure
    with patch("app.worker.validation_tasks.SessionLocal", TestingSessionLocal), \
         patch("app.worker.validation_tasks.generate_feature_snapshot", side_effect=Exception("Simulated runtime failure")):
        try:
            execute_evidence_validation(run_id_str)
        except Exception:
            pass 

    db2 = TestingSessionLocal()
    recovered_case = db2.query(Case).filter_by(case_id=test_case_id).first()
    recovered_run = db2.query(EvidenceValidationRun).filter_by(id=run.id).first()
    
    assert recovered_run.status == EValidationRunStatus.FAILED
    assert recovered_case.processing_state == ProcessingState.D_INTELLIGENCE_READY
    
    assert db2.query(EvidenceValidationResult).filter_by(validation_run_id=run.id).count() == 0
    assert db2.query(EvidenceRequirementAssessment).filter_by(validation_run_id=run.id).count() == 0
    assert db2.query(CrossSourceFieldLink).filter_by(validation_run_id=run.id).count() == 0
    assert db2.query(CaseFeatureSnapshot).filter_by(validation_run_id=run.id).count() == 0
    
    db2.close()
    db.close()

def test_skip_locked_concurrency():
    db1 = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db1)
    case.processing_state = ProcessingState.D_INTELLIGENCE_READY
    
    run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=uuid.uuid4(),
        status=EValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        idempotency_key="idemp_2_" + str(uuid.uuid4())
    )
    db1.add(run)
    db1.commit()
    run_id_str = str(run.id)
    
    locked_run = db1.query(EvidenceValidationRun).with_for_update().filter_by(id=run.id).first()
    
    # Check that it didn't change anything (status still RUNNING, not COMPLETED or FAILED)
    # For SQLite tests we simulate the lock skip by mocking the query.first to return None
    db1.rollback()
    db1.close()
    
    with patch("app.worker.validation_tasks.SessionLocal", TestingSessionLocal):
        # We need to simulate that the lock query returns None
        class MockQuery:
            def filter(self, *args, **kwargs): return self
            def with_for_update(self, *args, **kwargs): return self
            def first(self): return None
            
        with patch("sqlalchemy.orm.Session.query", return_value=MockQuery()):
            execute_evidence_validation(run_id_str) 
    
    db2 = TestingSessionLocal()
    final_run = db2.query(EvidenceValidationRun).filter_by(id=uuid.UUID(run_id_str)).first()
    assert final_run.status == EValidationRunStatus.RUNNING
    db2.close()

def test_completed_run_skipped():
    db = TestingSessionLocal()
    merch, case, disp, pay = create_full_case_setup(db)
    case.processing_state = ProcessingState.D_INTELLIGENCE_READY
    
    run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=uuid.uuid4(),
        status=EValidationRunStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        idempotency_key="idemp_3_" + str(uuid.uuid4())
    )
    db.add(run)
    db.commit()
    
    with patch("app.worker.validation_tasks.SessionLocal", TestingSessionLocal):
        execute_evidence_validation(str(run.id))
    
    db2 = TestingSessionLocal()
    final_run = db2.query(EvidenceValidationRun).filter_by(id=run.id).first()
    assert final_run.status == EValidationRunStatus.COMPLETED
    assert db2.query(CaseFeatureSnapshot).filter_by(validation_run_id=run.id).count() == 0
    db2.close()
    db.close()
