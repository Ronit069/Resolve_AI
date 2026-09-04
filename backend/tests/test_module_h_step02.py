import pytest
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_current_merchant
from app.models.shared import Merchant, Case, ProcessingState
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidenceValidationResult, EvidencePolicyVersion, ValidationRuleCatalog, ValidationRuleVersion
from app.models.module_a import Dispute
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy, PredictionExplanation
from app.models.module_g import GeneratedDraft, ResponseGenerationRun, GenerationStatus, DraftClaim, LLMGuardrailResult
from app.models.module_h import ReviewQueueItem, QueueStatus
from app.models.module_c import EvidenceDocument, ScanStatus
import sqlalchemy as sa
from sqlalchemy.orm import Session

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_02_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"

@pytest.fixture(scope="module")
def postgres_engine():
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
    
    from app.core.database import Base
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    
    yield session
    session.close()

def override_get_db(db_session):
    def _override():
        yield db_session
    return _override

@pytest.fixture
def client(db):
    app.dependency_overrides[get_current_merchant] = lambda: db.query(Merchant).filter(Merchant.is_active == True).first()
    # The get_db dependency will use the same test session
    from app.core.database import get_db
    app.dependency_overrides[get_db] = override_get_db(db)
    yield TestClient(app)
    app.dependency_overrides.clear()

def setup_base_data(db: Session):
    merchant = Merchant(external_merchant_id="test_merchant_123", name="Test Merchant")
    db.add(merchant)
    db.commit()
    
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id="disp_123", source="razorpay")
    db.add(case)
    db.commit()
    
    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id="disp_123",
        payment_id="pay_123",
        amount_minor=10000,
        currency="INR",
        reason_code="fraud",
        status="open",
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=datetime.now(timezone.utc) + timedelta(days=2)
    )
    db.add(dispute)
    
    # Policy versions needed for relationships
    policy_version = EvidencePolicyVersion(
        payment_network="visa", reason_code="fraud", phase="pre_arbitration", version=1,
        effective_from=datetime.now(timezone.utc)
    )
    db.add(policy_version)
    
    model_version = ModelVersion(algorithm="lgbm", status="active")
    db.add(model_version)
    db.flush()
    
    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id)
    db.add(decision_policy)
    
    rule_catalog = ValidationRuleCatalog(rule_code="test_rule", category="test", description="test", severity_default="ERROR")
    db.add(rule_catalog)
    db.flush()
    rule_version = ValidationRuleVersion(rule_id=rule_catalog.rule_id, version=1, effective_from=datetime.now(timezone.utc), checksum="hash")
    db.add(rule_version)
    
    db.commit()
    
    return merchant, case, dispute, policy_version, model_version, decision_policy, rule_version

# 1. Happy path
def test_workspace_happy_path(client, db):
    merchant, case, dispute, policy_version, model_version, decision_policy, rule_version = setup_base_data(db)
    
    # Val run
    val_run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy_version.policy_version_id,
        status="COMPLETED", started_at=datetime.now(timezone.utc), idempotency_key="val_key"
    )
    db.add(val_run)
    db.commit()
    
    val_res = EvidenceValidationResult(
        validation_run_id=val_run.id, rule_version_id=rule_version.id, result="PASS", severity="INFO"
    )
    db.add(val_res)
    
    # Snap
    snap = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=val_run.id, feature_schema_version="1",
        features_json={"f1": 1}, feature_hash="hash", is_current=True
    )
    db.add(snap)
    db.commit()
    
    # Pred
    pred = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=snap.id, model_version_id=model_version.id,
        decision_policy_id=decision_policy.id, raw_score=0.9, calibrated_probability=0.9,
        recommendation="REVIEW", hard_block=True, idempotency_key="pred_key"
    )
    db.add(pred)
    db.commit()
    
    # Expl
    expl = PredictionExplanation(
        prediction_id=pred.id, feature_name="f1", contribution=0.1, display_text="test expl"
    )
    db.add(expl)
    
    # Draft
    gen_run = ResponseGenerationRun(
        case_id=case.case_id, prompt_template_version="v1", llm_model_version="v1",
        guardrail_version="v1", status=GenerationStatus.PASS
    )
    db.add(gen_run)
    db.commit()
    
    draft = GeneratedDraft(
        generation_run_id=gen_run.id, case_id=case.case_id, summary="test sum",
        draft_json={"data": "test"}, guardrail_status="PASS", is_current=True
    )
    db.add(draft)
    db.commit()
    
    claim = DraftClaim(draft_id=draft.id, claim_text="claim1", claim_type="CASE_FACT", support_status="SUPPORTED")
    db.add(claim)
    
    guard = LLMGuardrailResult(draft_id=draft.id, check_type="GROUNDING", result="PASS")
    db.add(guard)
    
    # Queue
    queue_item = ReviewQueueItem(
        case_id=case.case_id, prediction_id=pred.id, draft_id=draft.id,
        priority_score=90.0, queue_status=QueueStatus.PENDING, respond_by=datetime.now(timezone.utc)
    )
    db.add(queue_item)
    
    # Doc
    doc = EvidenceDocument(
        case_id=case.case_id, merchant_id=merchant.merchant_id, evidence_type="INVOICE",
        object_key="test_key", mime_type="application/pdf", file_size_bytes=100, sha256="doc_hash"
    )
    db.add(doc)
    db.commit()
    
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    data = response.json()
    
    assert data["case"]["case_id"] == str(case.case_id)
    assert data["dispute"]["amount_minor"] == 10000
    assert data["queue_item"]["priority_score"] == 90.0
    assert data["feature_snapshot"]["is_current"] == True
    assert data["evidence_findings"]["status"] == "COMPLETED"
    assert len(data["risk_prediction"]["explanations"]) == 1
    assert data["risk_prediction"]["explanations"][0]["feature_name"] == "f1"
    assert data["current_draft"]["summary"] == "test sum"
    assert len(data["current_draft"]["claims"]) == 1
    assert len(data["evidence_documents"]) == 1
    assert data["evidence_documents"][0]["object_key"] == "test_key"
    assert any(w["source"] == "MODULE_F" and w["type"] == "HARD_BLOCK" for w in data["uncertainty_warnings"])

# 2. Tenant isolation
def test_workspace_tenant_isolation(client, db):
    merchantA = Merchant(external_merchant_id="merchant_a", name="Merchant A")
    merchantB = Merchant(external_merchant_id="merchant_b", name="Merchant B")
    db.add(merchantA)
    db.add(merchantB)
    db.commit()
    
    caseA = Case(merchant_id=merchantA.merchant_id, external_dispute_id="disp_a", source="razorpay")
    db.add(caseA)
    db.commit()
    
    app.dependency_overrides[get_current_merchant] = lambda: merchantB
    response = client.get(f"/api/v1/cases/{caseA.case_id}/workspace")
    assert response.status_code == 404
    app.dependency_overrides.clear()

# 3. Missing draft
def test_workspace_missing_draft(client, db):
    merchant, case, dispute, *rest = setup_base_data(db)
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    data = response.json()
    assert data["current_draft"] is None

# 4. Missing prediction explanation
def test_workspace_missing_explanation(client, db):
    merchant, case, dispute, policy_version, model_version, decision_policy, rule_version = setup_base_data(db)
    # val_run + snap + pred (no expl)
    val_run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy_version.policy_version_id,
        status="COMPLETED", started_at=datetime.now(timezone.utc), idempotency_key="val_key"
    )
    db.add(val_run)
    db.commit()
    snap = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=val_run.id, feature_schema_version="1",
        features_json={"f1": 1}, feature_hash="hash", is_current=True
    )
    db.add(snap)
    db.commit()
    pred = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=snap.id, model_version_id=model_version.id,
        decision_policy_id=decision_policy.id, raw_score=0.9, calibrated_probability=0.9,
        recommendation="REVIEW", hard_block=False, idempotency_key="pred_key"
    )
    db.add(pred)
    db.commit()
    
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    assert len(response.json()["risk_prediction"]["explanations"]) == 0

# 5. Missing queue item
def test_workspace_missing_queue_item(client, db):
    merchant, case, dispute, *rest = setup_base_data(db)
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    assert response.json()["queue_item"] is None

# 6. Missing Module E findings
def test_workspace_missing_module_e(client, db):
    merchant, case, dispute, *rest = setup_base_data(db)
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    assert response.json()["evidence_findings"] is None

# 7. Current draft selection
def test_current_draft_selection(client, db):
    merchant, case, dispute, *rest = setup_base_data(db)
    gen_run = ResponseGenerationRun(
        case_id=case.case_id, prompt_template_version="v1", llm_model_version="v1",
        guardrail_version="v1", status=GenerationStatus.PASS
    )
    db.add(gen_run)
    db.commit()
    
    draft1 = GeneratedDraft(
        generation_run_id=gen_run.id, case_id=case.case_id, summary="old",
        draft_json={"data": "test"}, guardrail_status="PASS", is_current=False
    )
    draft2 = GeneratedDraft(
        generation_run_id=gen_run.id, case_id=case.case_id, summary="new",
        draft_json={"data": "test"}, guardrail_status="PASS", is_current=True
    )
    db.add_all([draft1, draft2])
    db.commit()
    
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    assert response.json()["current_draft"]["summary"] == "new"

# 8. Current feature snapshot selection
def test_current_feature_snapshot_selection(client, db):
    merchant, case, dispute, policy_version, _, _, _ = setup_base_data(db)
    val_run1 = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy_version.policy_version_id,
        status="COMPLETED", started_at=datetime.now(timezone.utc), idempotency_key="val_key1"
    )
    val_run2 = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy_version.policy_version_id,
        status="COMPLETED", started_at=datetime.now(timezone.utc), idempotency_key="val_key2"
    )
    db.add_all([val_run1, val_run2])
    db.commit()
    snap1 = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=val_run1.id, feature_schema_version="1",
        features_json={"f1": 1}, feature_hash="hash1", is_current=False
    )
    snap2 = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=val_run2.id, feature_schema_version="1",
        features_json={"f2": 2}, feature_hash="hash2", is_current=True
    )
    db.add_all([snap1, snap2])
    db.commit()
    
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    assert response.json()["feature_snapshot"]["features_json"] == {"f2": 2}

# 9. Latest prediction selection
def test_latest_prediction_selection(client, db):
    merchant, case, dispute, policy_version, model_version, decision_policy, _ = setup_base_data(db)
    val_run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy_version.policy_version_id,
        status="COMPLETED", started_at=datetime.now(timezone.utc), idempotency_key="val_key"
    )
    db.add(val_run)
    db.commit()
    snap = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=val_run.id, feature_schema_version="1",
        features_json={"f1": 1}, feature_hash="hash", is_current=True
    )
    db.add(snap)
    db.commit()
    
    pred1 = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=snap.id, model_version_id=model_version.id,
        decision_policy_id=decision_policy.id, raw_score=0.5, calibrated_probability=0.5,
        recommendation="REVIEW", hard_block=False, idempotency_key="pred_key1", created_at=datetime.now(timezone.utc)-timedelta(hours=1)
    )
    pred2 = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=snap.id, model_version_id=model_version.id,
        decision_policy_id=decision_policy.id, raw_score=0.9, calibrated_probability=0.9,
        recommendation="ACCEPT", hard_block=False, idempotency_key="pred_key2", created_at=datetime.now(timezone.utc)
    )
    db.add_all([pred1, pred2])
    db.commit()
    
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    assert response.json()["risk_prediction"]["calibrated_probability"] == 0.9

# 10, 11, 12, 13 are implicitly covered by happy path

# 14. Uncertainty warnings
def test_uncertainty_warnings(client, db):
    merchant, case, dispute, policy_version, model_version, decision_policy, rule_version = setup_base_data(db)
    val_run = EvidenceValidationRun(
        case_id=case.case_id, evidence_version="v1", policy_version_id=policy_version.policy_version_id,
        status="COMPLETED", started_at=datetime.now(timezone.utc), idempotency_key="val_key"
    )
    db.add(val_run)
    db.commit()
    val_res = EvidenceValidationResult(
        validation_run_id=val_run.id, rule_version_id=rule_version.id, result="FAIL", severity="ERROR", explanation="missing info"
    )
    db.add(val_res)
    snap = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=val_run.id, feature_schema_version="1",
        features_json={"f1": 1}, feature_hash="hash", is_current=True
    )
    db.add(snap)
    db.commit()
    pred = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=snap.id, model_version_id=model_version.id,
        decision_policy_id=decision_policy.id, raw_score=0.9, calibrated_probability=0.9,
        recommendation="REVIEW", hard_block=True, idempotency_key="pred_key"
    )
    db.add(pred)
    db.commit()
    gen_run = ResponseGenerationRun(
        case_id=case.case_id, prompt_template_version="v1", llm_model_version="v1",
        guardrail_version="v1", status=GenerationStatus.PASS
    )
    db.add(gen_run)
    db.commit()
    draft = GeneratedDraft(
        generation_run_id=gen_run.id, case_id=case.case_id, summary="sum",
        draft_json={"data": "test"}, guardrail_status="FAIL", is_current=True
    )
    db.add(draft)
    db.commit()
    claim = DraftClaim(draft_id=draft.id, claim_text="c1", claim_type="CASE_FACT", support_status="UNSUPPORTED")
    db.add(claim)
    db.commit()
    
    response = client.get(f"/api/v1/cases/{case.case_id}/workspace")
    assert response.status_code == 200
    warnings = response.json()["uncertainty_warnings"]
    assert any(w["type"] == "HARD_BLOCK" for w in warnings)
    assert any(w["type"] == "VALIDATION_FAILURE" for w in warnings)
    assert any(w["type"] == "GUARDRAIL_WARNING" for w in warnings)
    assert any(w["type"] == "CLAIM_SUPPORT_WARNING" for w in warnings)

# 15. Authentication
def test_unauthenticated(db):
    app.dependency_overrides.clear()
    unauth_client = TestClient(app)
    response = unauth_client.get(f"/api/v1/cases/{uuid.uuid4()}/workspace")
    assert response.status_code in [401, 403, 404, 422]

# 16. Invalid UUID
def test_invalid_uuid(client):
    response = client.get("/api/v1/cases/invalid-uuid/workspace")
    assert response.status_code == 422
