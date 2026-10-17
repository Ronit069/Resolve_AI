"""
Tests for issue #11 — training/live feature-distribution drift monitoring:
app/services/observability/drift_monitoring.py and the
GET /api/v1/observability/drift endpoint.

Mirrors test_module_l_model_evaluation.py's pattern: a minimal FastAPI
app around only drift_monitoring.router (avoiding app.main's unrelated
storage import), the real auth/role chain, and a real SQLite session.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.api.endpoints import drift_monitoring as drift_endpoint
from app.models.shared import Merchant, AppUser, AppUserRole
from app.models.module_e import CaseFeatureSnapshot, EvidenceValidationRun, EvidencePolicyVersion
from app.models.module_f import RiskPrediction, ModelVersion, ModelDecisionPolicy
from app.services.observability.drift_monitoring import (
    compute_psi,
    compute_drift_report,
    load_reference_distribution,
    get_live_feature_rows,
    check_drift,
    DriftReferenceUnavailable,
)

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_drift_monitoring.db"


# ---------------------------------------------------------------------------
# Pure PSI math -- no DB, no disk.
# ---------------------------------------------------------------------------

def test_psi_near_zero_for_identical_distributions():
    ref = [float(i % 20) for i in range(200)]
    cur = list(ref)
    psi = compute_psi(ref, cur)
    assert psi is not None
    assert psi < 0.02


def test_psi_significant_for_clearly_shifted_distribution():
    ref = [float(i % 20) for i in range(200)]
    cur = [v + 40.0 for v in ref]  # entirely out of the reference's range
    psi = compute_psi(ref, cur)
    assert psi is not None
    assert psi >= 0.2


def test_psi_none_when_either_sample_too_small():
    ref = [1.0, 2.0, 3.0]  # below MIN_OBSERVATIONS
    cur = [float(i) for i in range(50)]
    assert compute_psi(ref, cur) is None
    assert compute_psi(cur, ref) is None


def test_psi_none_for_degenerate_constant_reference():
    ref = [5.0] * 50
    cur = [float(i) for i in range(50)]
    assert compute_psi(ref, cur) is None


# ---------------------------------------------------------------------------
# compute_drift_report -- pure, given reference + current rows.
# ---------------------------------------------------------------------------

def test_drift_report_structure_and_severity_classification():
    reference_by_feature = {
        "dispute_amount": [float(i) for i in range(100)],
        "days_to_deadline": [10.0] * 60,  # degenerate -> always INSUFFICIENT_DATA
    }
    current_rows = [{"dispute_amount": float(i) + 500.0} for i in range(100)]  # shifted hard

    report = compute_drift_report(reference_by_feature, current_rows)

    assert report["live_sample_count"] == 100
    assert "dispute_amount" in report["features"]
    assert report["features"]["dispute_amount"]["severity"] == "SIGNIFICANT_DRIFT"
    assert report["features"]["dispute_amount"]["psi"] >= 0.2
    assert "dispute_amount" in report["drifted_features"]

    # days_to_deadline has no non-null values in current_rows at all.
    assert report["features"]["days_to_deadline"]["current_count"] == 0
    assert report["features"]["days_to_deadline"]["severity"] == "INSUFFICIENT_DATA"
    assert "days_to_deadline" not in report["drifted_features"]

    assert report["overall_severity"] == "SIGNIFICANT_DRIFT"
    assert report["max_psi"] == pytest.approx(report["features"]["dispute_amount"]["psi"])


def test_drift_report_insufficient_data_when_no_live_rows():
    reference_by_feature = {"dispute_amount": [float(i) for i in range(50)]}
    report = compute_drift_report(reference_by_feature, [])
    assert report["live_sample_count"] == 0
    assert report["overall_severity"] == "INSUFFICIENT_DATA"
    assert report["max_psi"] is None
    assert report["drifted_features"] == []


def test_drift_report_stable_for_similar_distribution():
    reference_by_feature = {"dispute_amount": [float(i % 30) for i in range(300)]}
    current_rows = [{"dispute_amount": float(i % 30)} for i in range(300)]
    report = compute_drift_report(reference_by_feature, current_rows)
    assert report["features"]["dispute_amount"]["severity"] == "STABLE"
    assert report["drifted_features"] == []


# ---------------------------------------------------------------------------
# load_reference_distribution -- reads a local jsonl file (issue #9's own
# training-data convention), never fabricates a report when missing.
# ---------------------------------------------------------------------------

def test_load_reference_distribution_raises_when_file_missing(tmp_path):
    missing_path = str(tmp_path / "does_not_exist.jsonl")
    with pytest.raises(DriftReferenceUnavailable):
        load_reference_distribution(path=missing_path)


def test_load_reference_distribution_parses_numeric_features_only(tmp_path):
    path = tmp_path / "train_sample.jsonl"
    rows = [
        {"features": {"dispute_amount": 100.0, "reason_code": "10.4", "amount_match": True}},
        {"features": {"dispute_amount": None, "reason_code": "13.1", "amount_match": False}},
        {"features": {"dispute_amount": 300.0, "reason_code": "13.3"}},
    ]
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    values = load_reference_distribution(path=str(path))
    assert values["dispute_amount"] == [100.0, 300.0]  # None skipped
    assert "reason_code" not in values  # not a numeric feature
    assert "amount_match" not in values  # boolean, not in numerical_features


# ---------------------------------------------------------------------------
# DB-backed: get_live_feature_rows / check_drift.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    if os.path.exists("test_drift_monitoring.db"):
        os.remove("test_drift_monitoring.db")
    eng = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    if os.path.exists("test_drift_monitoring.db"):
        os.remove("test_drift_monitoring.db")


@pytest.fixture()
def db_session(engine):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def _make_scored_case(db, dispute_amount, created_at):
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant", is_active=True)
    db.add(merchant)
    db.commit()

    from app.models.shared import Case
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="synthetic")
    db.add(case)
    db.commit()

    policy = db.query(EvidencePolicyVersion).filter_by(
        payment_network="Visa", reason_code="10.4", phase="chargeback"
    ).first()
    if policy is None:
        policy = EvidencePolicyVersion(
            payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
            effective_from=datetime.now(timezone.utc),
        )
        db.add(policy)
        db.commit()
    run = EvidenceValidationRun(
        case_id=case.case_id, status="COMPLETED", evidence_version="v1",
        policy_version_id=policy.policy_version_id, started_at=datetime.now(timezone.utc),
        idempotency_key=str(uuid.uuid4()),
    )
    db.add(run)
    db.commit()

    snapshot = CaseFeatureSnapshot(
        case_id=case.case_id, validation_run_id=run.id, feature_schema_version="ml_features_v1",
        features_json={"dispute_amount": dispute_amount}, feature_hash=f"hash_{uuid.uuid4()}",
    )
    db.add(snapshot)
    db.commit()

    model_version = ModelVersion(algorithm="CatBoost", status="champion")
    db.add(model_version)
    db.commit()
    decision_policy = ModelDecisionPolicy(model_version_id=model_version.id, active=True)
    db.add(decision_policy)
    db.commit()

    prediction = RiskPrediction(
        case_id=case.case_id, feature_snapshot_id=snapshot.id, model_version_id=model_version.id,
        decision_policy_id=decision_policy.id, raw_score=0.5, calibrated_probability=0.5,
        recommendation="REVIEW", hard_block=False, idempotency_key=f"pred_{uuid.uuid4()}",
        created_at=created_at,
    )
    db.add(prediction)
    db.commit()
    return prediction


def test_get_live_feature_rows_returns_most_recent_first_and_respects_limit(db_session):
    for i in range(5):
        _make_scored_case(db_session, dispute_amount=float(i), created_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc))

    rows = get_live_feature_rows(db_session, limit=3)
    assert len(rows) == 3
    # Most recently created (Jan 5) first.
    assert [r["dispute_amount"] for r in rows] == [4.0, 3.0, 2.0]


def test_get_live_feature_rows_empty_when_no_predictions(db_session):
    assert get_live_feature_rows(db_session) == []


def test_check_drift_raises_when_no_reference_on_disk(db_session, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DriftReferenceUnavailable):
        check_drift(db_session)


def test_check_drift_end_to_end_with_real_reference_file(db_session, monkeypatch, tmp_path):
    train_path = tmp_path / "synthetic_benchmark_v1_train.jsonl"
    with open(train_path, "w") as f:
        for i in range(100):
            f.write(json.dumps({"features": {"dispute_amount": float(i % 50)}}) + "\n")
    monkeypatch.chdir(tmp_path)

    from datetime import timedelta
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        _make_scored_case(db_session, dispute_amount=float(i % 50), created_at=base + timedelta(hours=i))

    report = check_drift(db_session, limit=100)
    assert report["live_sample_count"] == 50
    assert report["features"]["dispute_amount"]["severity"] == "STABLE"


# ---------------------------------------------------------------------------
# Endpoint: real auth/role gating + 503 on missing reference.
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app = FastAPI()
    app.include_router(drift_endpoint.router, prefix="/api/v1/observability")

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _make_merchant(db):
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant", is_active=True)
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return merchant


def _make_user(db, merchant, role):
    user = AppUser(merchant_id=merchant.merchant_id, email=f"{uuid.uuid4()}@test.com", role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_endpoint_returns_503_when_no_reference_on_disk(client, db_session, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    merchant = _make_merchant(db_session)
    user = _make_user(db_session, merchant, AppUserRole.APPROVER)

    response = client.get("/api/v1/observability/drift", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 503
    assert "detail" in response.json()


def test_endpoint_returns_report_when_reference_available(client, db_session, monkeypatch, tmp_path):
    train_path = tmp_path / "synthetic_benchmark_v1_train.jsonl"
    with open(train_path, "w") as f:
        for i in range(100):
            f.write(json.dumps({"features": {"dispute_amount": float(i % 50)}}) + "\n")
    monkeypatch.chdir(tmp_path)

    merchant = _make_merchant(db_session)
    user = _make_user(db_session, merchant, AppUserRole.RISK_ANALYST)

    response = client.get("/api/v1/observability/drift", headers={"X-User-Id": str(user.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert body["live_sample_count"] == 0
    assert body["overall_severity"] == "INSUFFICIENT_DATA"


def test_endpoint_unauthenticated_request_is_rejected(client):
    response = client.get("/api/v1/observability/drift")
    assert response.status_code == 401
