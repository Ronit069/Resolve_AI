import pytest
import uuid
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy, MLDataset, MLDatasetMember, MLLabel, ModelTrainingRun, ModelMetric, PredictionExplanation
from app.models.shared import Case, Merchant
from app.models.module_a import *
from app.models.module_b import *
from app.models.module_c import *
from app.models.module_d import *
from app.models.module_g import *
from app.models.shared import Case, Merchant
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_f.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    yield session
    session.close()

def test_step18_tables_exist(db_session):
    # Verify we can query the new tables without errors
    db_session.query(RiskPrediction).limit(1).all()
    db_session.query(ModelVersion).limit(1).all()
    db_session.query(ModelDecisionPolicy).limit(1).all()
    db_session.query(MLDataset).limit(1).all()
    db_session.query(MLDatasetMember).limit(1).all()
    db_session.query(MLLabel).limit(1).all()
    db_session.query(ModelTrainingRun).limit(1).all()
    db_session.query(ModelMetric).limit(1).all()
    db_session.query(PredictionExplanation).limit(1).all()

def test_risk_prediction_schema_and_fks(db_session):
    # Create dependencies
    merchant = Merchant(name="Test Merchant F18", external_merchant_id="ext_f18_1")
    db_session.add(merchant)
    db_session.commit()
    
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id="disp_f18_1", source="synthetic")
    db_session.add(case)
    db_session.commit()
    
    policy = EvidencePolicyVersion(payment_network="visa", reason_code="10.4", phase="PRE", version=2, effective_from=datetime.now(timezone.utc))
    db_session.add(policy)
    db_session.commit()
    val_run = EvidenceValidationRun(case_id=case.case_id, status="COMPLETED", evidence_version=1, policy_version_id=policy.policy_version_id, started_at=datetime.now(timezone.utc), idempotency_key=str(uuid.uuid4()))
    db_session.add(val_run)
    db_session.commit()

    feature_snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=val_run.id,
        feature_schema_version="v1",
        features_json={"f1": 1},
        feature_hash="hash_1"
    )
    db_session.add(feature_snapshot)
    
    model_version = ModelVersion(algorithm="catboost", status="ACTIVE")
    db_session.add(model_version)
    
    db_session.commit()
    
    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id, active=True)
    db_session.add(decision_policy)
    db_session.commit()
    
    # Create risk prediction
    prediction = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=feature_snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=0.85,
        calibrated_probability=0.82,
        recommendation="REVIEW",
        hard_block=False,
        idempotency_key="key_123"
    )
    db_session.add(prediction)
    db_session.commit()
    
    # Retrieve and check
    retrieved = db_session.query(RiskPrediction).filter_by(idempotency_key="key_123").first()
    assert retrieved is not None
    assert retrieved.case_id == case.case_id
    assert float(retrieved.raw_score) == 0.85
    assert float(retrieved.calibrated_probability) == 0.82
    assert retrieved.recommendation == "REVIEW"
    assert retrieved.hard_block is False
    assert isinstance(retrieved.id, uuid.UUID)

def test_risk_prediction_idempotency(db_session):
    # Create dependencies
    merchant = Merchant(name="Test Merchant F18 ID", external_merchant_id="ext_f18_2")
    db_session.add(merchant)
    db_session.commit()
    
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id="disp_f18_2", source="synthetic")
    db_session.add(case)
    db_session.commit()
    
    policy = EvidencePolicyVersion(payment_network="visa", reason_code="10.4", phase="PRE", version=3, effective_from=datetime.now(timezone.utc))
    db_session.add(policy)
    db_session.commit()
    val_run = EvidenceValidationRun(case_id=case.case_id, status="COMPLETED", evidence_version=1, policy_version_id=policy.policy_version_id, started_at=datetime.now(timezone.utc), idempotency_key=str(uuid.uuid4()))
    db_session.add(val_run)
    db_session.commit()

    feature_snapshot = CaseFeatureSnapshot(
        case_id=case.case_id,
        validation_run_id=val_run.id,
        feature_schema_version="v1",
        features_json={"f1": 1},
        feature_hash="hash_2"
    )
    db_session.add(feature_snapshot)
    
    model_version = ModelVersion(algorithm="catboost", status="ACTIVE")
    db_session.add(model_version)
    db_session.commit()
    
    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id, active=True)
    db_session.add(decision_policy)
    db_session.commit()
    
    # Create first prediction
    pred1 = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=feature_snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=0.9,
        calibrated_probability=0.9,
        recommendation="CONTEST",
        hard_block=False,
        idempotency_key="unique_key_456"
    )
    db_session.add(pred1)
    db_session.commit()
    
    # Create second prediction with same idempotency key
    pred2 = RiskPrediction(
        case_id=case.case_id,
        feature_snapshot_id=feature_snapshot.id,
        model_version_id=model_version.id,
        decision_policy_id=decision_policy.id,
        raw_score=0.9,
        calibrated_probability=0.9,
        recommendation="CONTEST",
        hard_block=False,
        idempotency_key="unique_key_456"
    )
    db_session.add(pred2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
