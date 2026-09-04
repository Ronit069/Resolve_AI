"""
Phase 2 — tests for the authoritative Step 15 evaluation exposure:
app/services/mlops/evaluation_artifact.py and the
GET /api/v1/observability/model-evaluation endpoint.

Deliberately does NOT import app.main (unlike
test_module_l_deployment_mlops.py). app.main transitively imports
app.core.storage, which eagerly instantiates a boto3 S3 client and dials
MinIO at import time — a pre-existing, unrelated dependency that isn't
running in this environment (see the already-documented 16 errors in
test_module_l_deployment_mlops.py). Instead these tests build a minimal
FastAPI app around ONLY model_evaluation.router, using the real,
unmodified app.api.deps auth chain (get_current_user /
get_current_merchant / require_role) and a real SQLite-backed session —
this exercises the actual endpoint and its real auth/role gating without
depending on the unrelated storage subsystem.
"""

import json
import os
import shutil
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.api.endpoints import model_evaluation
from app.models.shared import Merchant, AppUser, AppUserRole
from app.services.mlops.evaluation_artifact import (
    load_latest_evaluation,
    EvaluationArtifactUnavailable,
)

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_l_model_evaluation.db"
REAL_ARTIFACT_DIR = "artifacts"


@pytest.fixture(scope="module")
def engine():
    if os.path.exists("test_module_l_model_evaluation.db"):
        os.remove("test_module_l_model_evaluation.db")
    eng = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    if os.path.exists("test_module_l_model_evaluation.db"):
        os.remove("test_module_l_model_evaluation.db")


@pytest.fixture()
def db_session(engine):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client(engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app = FastAPI()
    app.include_router(model_evaluation.router, prefix="/api/v1/observability")

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


def _latest_real_artifact():
    """The real, already-generated Phase 1 artifact — read directly for comparison, never recomputed."""
    return load_latest_evaluation()


# ---------------------------------------------------------------------------
# 1 & 2. Artifact exists -> endpoint returns real metrics, matching the JSON.
# ---------------------------------------------------------------------------

def test_endpoint_returns_real_metrics_matching_artifact(client, db_session):
    merchant = _make_merchant(db_session)
    user = _make_user(db_session, merchant, AppUserRole.APPROVER)

    response = client.get(
        "/api/v1/observability/model-evaluation", headers={"X-User-Id": str(user.user_id)}
    )
    assert response.status_code == 200
    body = response.json()

    artifact = _latest_real_artifact()
    metrics = artifact["metrics"]
    holdout = artifact["test_holdout_dataset"]

    assert body["sample_count"] == holdout["example_count"]
    assert body["positive_count"] == holdout["positive_count"]
    assert body["negative_count"] == holdout["negative_count"]
    assert body["precision"] == pytest.approx(metrics["precision"])
    assert body["recall"] == pytest.approx(metrics["recall"])
    assert body["f1"] == pytest.approx(metrics["f1"])
    assert body["accuracy"] == pytest.approx(metrics["accuracy"])
    assert body["confusion_matrix"] == metrics["confusion_matrix"]
    assert body["false_positive_count"] == metrics["confusion_matrix"]["fp"]
    assert body["expected_cost"] == pytest.approx(metrics["expected_cost"])
    assert body["accept_count"] == metrics["accept_count"]
    assert body["review_count"] == metrics["review_count"]
    assert body["contest_count"] == metrics["contest_count"]
    assert body["brier_raw"] == pytest.approx(metrics["brier_score_raw"])
    assert body["brier_calibrated"] == pytest.approx(metrics["brier_score_calibrated"])


# ---------------------------------------------------------------------------
# 3. Model/provenance fields exposed correctly.
# ---------------------------------------------------------------------------

def test_endpoint_exposes_provenance_fields(client, db_session):
    merchant = _make_merchant(db_session)
    user = _make_user(db_session, merchant, AppUserRole.RISK_ANALYST)

    response = client.get(
        "/api/v1/observability/model-evaluation", headers={"X-User-Id": str(user.user_id)}
    )
    assert response.status_code == 200
    body = response.json()

    artifact = _latest_real_artifact()
    assert body["model"]["algorithm"] == artifact["champion_model"]["algorithm"]
    assert body["model"]["run_id"] == artifact["champion_model"]["run_dir"]
    assert body["model"]["model_sha256"] == artifact["champion_model"]["model_cbm_sha256"]
    assert len(body["model"]["model_sha256"]) == 64

    assert body["evaluation"]["holdout_file"] == artifact["test_holdout_dataset"]["file"]
    assert body["evaluation"]["holdout_sha256"] == artifact["test_holdout_dataset"]["sha256"]
    assert body["evaluation"]["evaluation_timestamp"] == artifact["timestamp"]
    assert body["evaluation"]["calibration_method"] == artifact["calibration"]["calibration_method"]
    assert body["evaluation"]["policy_id"] == artifact["decision_policy"]["step14_dir"]


# ---------------------------------------------------------------------------
# 4. Missing artifact -> honest unavailable response, never fabricated zeros.
# ---------------------------------------------------------------------------

def test_missing_artifact_returns_honest_unavailable_response(client, db_session, tmp_path, monkeypatch):
    merchant = _make_merchant(db_session)
    user = _make_user(db_session, merchant, AppUserRole.APPROVER)

    empty_dir = tmp_path / "no_artifacts_here"
    empty_dir.mkdir()
    from app.core.config import settings

    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(empty_dir))

    response = client.get(
        "/api/v1/observability/model-evaluation", headers={"X-User-Id": str(user.user_id)}
    )
    assert response.status_code == 503
    body = response.json()
    assert "detail" in body
    # Never a 200 with fabricated placeholder numbers.
    assert "precision" not in body
    assert "0.0" not in json.dumps(body)  # no stray fabricated zero metric


def test_loader_raises_when_no_artifact_directory_exists(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MODEL_EVALUATION_ARTIFACT_DIR", str(tmp_path / "does_not_exist"))
    with pytest.raises(EvaluationArtifactUnavailable):
        load_latest_evaluation()


# ---------------------------------------------------------------------------
# 5. No hard-coded metrics in the endpoint implementation.
# ---------------------------------------------------------------------------

def test_endpoint_implementation_contains_no_hardcoded_metric_literals():
    endpoint_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "api", "endpoints", "model_evaluation.py",
    )
    with open(endpoint_path) as f:
        content = f.read()

    # The real observed Phase 1 values must never appear as literals in
    # the endpoint source — every number must come from the artifact.
    forbidden_literals = [
        "1408", "444", "964", "0.5900900900900901", "0.7422096317280453",
        "0.8707386363636364", "262", "182", "2020.0", "742", "404",
        "0.08203449135087003", "0.07149647116098277",
        "90a79898da06a8e529d08f9e8fe117d67f2133572d7327a3aff89eed00f04bcd",
    ]
    for literal in forbidden_literals:
        assert literal not in content, f"hard-coded evaluation value found in endpoint source: {literal}"


def test_service_module_never_computes_metrics_only_reads_json():
    service_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "services", "mlops", "evaluation_artifact.py",
    )
    with open(service_path) as f:
        content = f.read()
    for forbidden in ("predict", "fit(", "CatBoost", "IsotonicRegression", "calculate_3way_cost"):
        assert forbidden not in content


# ---------------------------------------------------------------------------
# 6. Existing auth/role behavior preserved.
# ---------------------------------------------------------------------------

def test_unauthenticated_request_is_rejected(client):
    response = client.get("/api/v1/observability/model-evaluation")
    assert response.status_code == 422  # missing required X-User-Id header, same convention as sibling endpoints


def test_unknown_user_id_is_rejected(client):
    response = client.get(
        "/api/v1/observability/model-evaluation", headers={"X-User-Id": str(uuid.uuid4())}
    )
    assert response.status_code == 401


def test_role_not_in_allowed_list_is_forbidden(client, db_session):
    merchant = _make_merchant(db_session)
    # No CUSTOMER_SUPPORT-equivalent unauthorized role exists on AppUserRole
    # in this schema other than the three allowed ones plus any others the
    # model defines; use the sibling endpoints' own exhaustive allow-list
    # convention and simply assert an allowed role succeeds while a role
    # object crafted outside AppUserRole's allowed values is rejected by
    # require_role's own logic (mirrors test_module_l_deployment_mlops.py's
    # own role-gating convention for the sibling runtime-metrics endpoint).
    from app.models.shared import AppUserRole as Role
    allowed = {Role.MERCHANT_ADMIN, Role.RISK_ANALYST, Role.APPROVER}
    all_roles = set(Role)
    disallowed = all_roles - allowed
    if not disallowed:
        pytest.skip("No role outside the allowed list exists on AppUserRole to test rejection with.")
    other_role = sorted(disallowed, key=lambda r: r.value)[0]
    user = _make_user(db_session, merchant, other_role)

    response = client.get(
        "/api/v1/observability/model-evaluation", headers={"X-User-Id": str(user.user_id)}
    )
    assert response.status_code == 403


def test_allowed_roles_all_succeed(client, db_session):
    merchant = _make_merchant(db_session)
    for role in (AppUserRole.MERCHANT_ADMIN, AppUserRole.RISK_ANALYST, AppUserRole.APPROVER):
        user = _make_user(db_session, merchant, role)
        response = client.get(
            "/api/v1/observability/model-evaluation", headers={"X-User-Id": str(user.user_id)}
        )
        assert response.status_code == 200, f"role {role} was unexpectedly rejected"
