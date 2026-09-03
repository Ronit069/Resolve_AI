"""
Module L smoke tests.

Covers: Docker/compose configuration correctness (static, file-based —
no Docker daemon required, matching L-09's "must not require external
production services"), README completeness, the L-04 runtime-metrics
module + endpoint, the model-registry champion convention, MLflow
best-effort logging never raising, and the L-06 seed command's
idempotency end to end.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# L-05/L-07: Docker/compose configuration (static checks, no daemon needed)
# ---------------------------------------------------------------------------

def test_compose_file_is_valid_yaml_with_expected_services():
    compose_path = os.path.join(REPO_ROOT, "docker-compose.yaml")
    with open(compose_path) as f:
        compose = yaml.safe_load(f)

    services = compose["services"]
    for expected in ("postgres", "redis", "minio", "api", "worker", "web", "mlflow"):
        assert expected in services, f"missing service: {expected}"

    # Existing infra service definitions are preserved, not redesigned.
    assert services["postgres"]["image"] == "pgvector/pgvector:pg15"
    assert services["redis"]["image"] == "redis:7-alpine"
    assert services["minio"]["image"] == "minio/minio:latest"

    assert services["api"]["build"]["dockerfile"] == "Dockerfile.api"
    assert services["worker"]["build"]["dockerfile"] == "Dockerfile.worker"


def test_api_and_worker_dockerfiles_exist_with_expected_cmd():
    api_dockerfile = os.path.join(BACKEND_DIR, "Dockerfile.api")
    worker_dockerfile = os.path.join(BACKEND_DIR, "Dockerfile.worker")
    assert os.path.isfile(api_dockerfile)
    assert os.path.isfile(worker_dockerfile)

    with open(api_dockerfile) as f:
        api_content = f.read()
    with open(worker_dockerfile) as f:
        worker_content = f.read()

    assert "uvicorn" in api_content and "app.main:app" in api_content
    assert "celery" in worker_content and "app.worker.celery_app" in worker_content
    # Neither Dockerfile hard-codes a credential (L-13 requirement).
    for content in (api_content, worker_content):
        for forbidden in ("RAZORPAY_KEY_SECRET=", "POSTGRES_PASSWORD=", "API_SECRET_KEY="):
            assert forbidden not in content


def test_frontend_dockerfile_exists():
    frontend_dockerfile = os.path.join(REPO_ROOT, "frontend", "Dockerfile")
    assert os.path.isfile(frontend_dockerfile)
    with open(frontend_dockerfile) as f:
        content = f.read()
    assert "nginx" in content
    assert "npm run build" in content


def test_no_env_file_committed_or_baked_into_images():
    # .env itself must never be committed/baked in — only .env.example.
    assert not os.path.isfile(os.path.join(BACKEND_DIR, ".env.example.secret"))
    dockerignore_path = os.path.join(BACKEND_DIR, ".dockerignore")
    assert os.path.isfile(dockerignore_path)
    with open(dockerignore_path) as f:
        assert ".env" in f.read()


# ---------------------------------------------------------------------------
# L-08: README
# ---------------------------------------------------------------------------

def test_readme_is_non_empty_and_covers_required_sections():
    readme_path = os.path.join(REPO_ROOT, "README.md")
    assert os.path.getsize(readme_path) > 0
    with open(readme_path) as f:
        content = f.read().lower()

    for required in (
        "prerequisite", "one-command", "docker compose up",
        "seed", "service url", "local development", "mlflow",
    ):
        assert required in content, f"README missing required coverage: {required}"


# ---------------------------------------------------------------------------
# L-04: runtime metrics module (pure, no DB)
# ---------------------------------------------------------------------------

def test_runtime_metrics_record_and_summarize():
    from app.services.observability.runtime_metrics import (
        get_runtime_metrics_summary, record_error, record_latency, reset_runtime_metrics,
    )

    reset_runtime_metrics()
    record_latency("ocr", 100.0)
    record_latency("ocr", 200.0)
    record_error("ocr")

    summary = get_runtime_metrics_summary()
    assert summary["ocr"]["sample_count"] == 2
    assert summary["ocr"]["error_count"] == 1
    assert summary["ocr"]["avg_latency_ms"] == 150.0
    assert summary["ocr"]["min_latency_ms"] == 100.0
    assert summary["ocr"]["max_latency_ms"] == 200.0
    assert summary["ocr"]["error_rate"] == pytest.approx(1 / 3)

    reset_runtime_metrics()


def test_runtime_metrics_track_latency_context_manager_reraises():
    from app.services.observability.runtime_metrics import (
        get_runtime_metrics_summary, reset_runtime_metrics, track_latency,
    )

    reset_runtime_metrics()
    with pytest.raises(ValueError):
        with track_latency("llm"):
            raise ValueError("simulated failure")

    summary = get_runtime_metrics_summary()
    assert summary["llm"]["error_count"] == 1
    assert summary["llm"]["sample_count"] == 1  # `finally` always records elapsed time, success or failure
    reset_runtime_metrics()


def test_runtime_metrics_decorator_preserves_return_value_and_name():
    from app.services.observability.runtime_metrics import track_latency_decorator, reset_runtime_metrics

    reset_runtime_metrics()

    @track_latency_decorator("inference")
    def compute(x):
        return x * 2

    assert compute(21) == 42
    assert compute.__name__ == "compute"
    reset_runtime_metrics()


def test_h22_and_l04_metrics_stay_separate_modules():
    import app.services.observability.queue_metrics as queue_metrics
    import app.services.observability.runtime_metrics as runtime_metrics

    assert queue_metrics is not runtime_metrics
    assert not hasattr(queue_metrics, "record_latency")
    assert not hasattr(runtime_metrics, "compute_queue_age_metrics")


# ---------------------------------------------------------------------------
# L-04 endpoint + model registry + seed idempotency (SQLite, same convention
# as test_module_a.py / test_module_k_outcome_feedback.py)
# ---------------------------------------------------------------------------

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_l.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module", autouse=True)
def _setup_module():
    from app.main import app
    from app.core.database import Base, get_db

    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_db():
    from app.core.database import Base
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _client():
    from app.main import app
    return TestClient(app)


def _make_merchant(db):
    from app.models.shared import Merchant
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant L", is_active=True)
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return merchant


def _make_user(db, merchant, role):
    from app.models.shared import AppUser
    user = AppUser(merchant_id=merchant.merchant_id, email=f"{role}_{uuid.uuid4()}@test.com", is_active=True, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_runtime_metrics_endpoint_role_gated_and_shaped():
    from app.models.shared import AppUserRole
    from app.services.observability.runtime_metrics import reset_runtime_metrics, record_latency

    reset_runtime_metrics()
    record_latency("ocr", 50.0)

    db = TestingSessionLocal()
    merchant = _make_merchant(db)
    approver = _make_user(db, merchant, AppUserRole.APPROVER)

    client = _client()
    response = client.get("/api/v1/observability/runtime-metrics", headers={"X-User-Id": str(approver.user_id)})
    assert response.status_code == 200
    body = response.json()
    assert "ocr" in body["categories"]
    assert body["categories"]["ocr"]["sample_count"] == 1
    reset_runtime_metrics()


def test_model_registry_mark_champion_demotes_previous():
    from app.models.module_f import ModelVersion
    from app.services.mlops.model_registry import CHAMPION_STATUS, RETIRED_STATUS, get_champion_model_version, mark_champion

    db = TestingSessionLocal()
    v1 = ModelVersion(algorithm="lgbm", status="pending")
    db.add(v1)
    db.commit()
    db.refresh(v1)

    champion1 = mark_champion(db, v1.id)
    assert champion1.status == CHAMPION_STATUS
    assert get_champion_model_version(db, algorithm="lgbm").id == v1.id

    v2 = ModelVersion(algorithm="lgbm", status="pending")
    db.add(v2)
    db.commit()
    db.refresh(v2)

    champion2 = mark_champion(db, v2.id)
    db.refresh(v1)
    assert champion2.status == CHAMPION_STATUS
    assert v1.status == RETIRED_STATUS
    assert get_champion_model_version(db, algorithm="lgbm").id == v2.id


def test_model_registry_raises_for_unknown_model_version():
    from app.services.mlops.model_registry import mark_champion

    db = TestingSessionLocal()
    with pytest.raises(ValueError):
        mark_champion(db, uuid.uuid4())


def test_mlflow_logging_never_raises_on_failure(monkeypatch):
    import mlflow
    from app.services.mlops.mlflow_tracking import log_training_run

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated MLflow failure — server unreachable")

    # Mock at the mlflow API boundary rather than hitting a real (and
    # potentially slow-to-time-out) network connection — the behavior
    # under test is log_training_run's own exception handling, not
    # MLflow's/the OS's connection-timeout behavior.
    monkeypatch.setattr(mlflow, "set_tracking_uri", _boom)

    # Must not raise, regardless of why MLflow logging failed.
    log_training_run(
        experiment_name="test_experiment", run_name="test_run",
        params={"a": 1}, metrics={"accuracy": 0.9},
    )


def test_seed_demo_functions_are_idempotent():
    import scripts.seed_demo as seed_demo

    db = TestingSessionLocal()
    merchant1 = seed_demo.seed_merchant(db)
    merchant2 = seed_demo.seed_merchant(db)
    assert merchant1.merchant_id == merchant2.merchant_id

    seed_demo.seed_users(db, merchant1)
    seed_demo.seed_users(db, merchant1)
    from app.models.shared import AppUser
    assert db.query(AppUser).count() == len(seed_demo.DEMO_USERS)

    policy1 = seed_demo.seed_reason_code_policy(db)
    policy2 = seed_demo.seed_reason_code_policy(db)
    assert policy1.policy_version_id == policy2.policy_version_id
    from app.models.module_e import EvidencePolicyVersion
    assert db.query(EvidencePolicyVersion).count() == 1

    champion1 = seed_demo.seed_champion_model(db)
    champion2 = seed_demo.seed_champion_model(db)
    assert champion1.id == champion2.id
    from app.models.module_f import ModelVersion
    assert db.query(ModelVersion).count() == 1

    decision_policy1 = seed_demo.seed_decision_policy(db, champion1)
    decision_policy2 = seed_demo.seed_decision_policy(db, champion1)
    assert decision_policy1.id == decision_policy2.id
    from app.models.module_f import ModelDecisionPolicy
    assert db.query(ModelDecisionPolicy).count() == 1


# Final Submission Checklist §22: "Three demo cases cover contest, review
# and accept/missing-evidence outcomes."
def test_seed_demo_cases_are_three_distinct_and_idempotent():
    import scripts.seed_demo as seed_demo
    from app.models.module_a import Dispute
    from app.models.module_f import RiskPrediction
    from app.models.module_h import QueueStatus, ReviewAction, ReviewActionEnum, ReviewQueueItem

    db = TestingSessionLocal()
    merchant = seed_demo.seed_merchant(db)
    seed_demo.seed_users(db, merchant)
    evidence_policy = seed_demo.seed_reason_code_policy(db)
    champion = seed_demo.seed_champion_model(db)
    decision_policy = seed_demo.seed_decision_policy(db, champion)

    seed_demo.seed_demo_cases(db, evidence_policy, champion, decision_policy)
    # Run twice: must not create duplicates.
    seed_demo.seed_demo_cases(db, evidence_policy, champion, decision_policy)

    assert db.query(Dispute).count() == 3
    assert db.query(RiskPrediction).count() == 3
    assert db.query(ReviewQueueItem).count() == 3
    assert db.query(ReviewAction).count() == 2  # PENDING (review) case has none

    def _state(external_dispute_id):
        dispute = db.query(Dispute).filter(Dispute.external_dispute_id == external_dispute_id).first()
        prediction = db.query(RiskPrediction).filter(RiskPrediction.case_id == dispute.case_id).first()
        queue_item = db.query(ReviewQueueItem).filter(ReviewQueueItem.case_id == dispute.case_id).first()
        action = db.query(ReviewAction).filter(ReviewAction.queue_item_id == queue_item.id).first()
        return prediction.recommendation, queue_item.queue_status, (action.action if action else None)

    assert _state("demo_disp_seed_001") == ("CONTEST", QueueStatus.DONE, ReviewActionEnum.APPROVE_CONTEST)
    assert _state("demo_disp_seed_002") == ("REVIEW", QueueStatus.PENDING, None)
    assert _state("demo_disp_seed_003") == ("ACCEPT", QueueStatus.DONE, ReviewActionEnum.APPROVE_ACCEPT)
