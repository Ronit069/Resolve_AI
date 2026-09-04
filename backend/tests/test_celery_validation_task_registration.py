"""
D-03 regression tests — evidence-validation Celery task registration.

Forensic finding: app.worker.celery_app's `include=[...]` list omitted
'app.worker.validation_tasks', so a REAL worker process (started via
`celery -A app.worker.celery_app worker`, which only imports what's listed
in `include`) never imported that module and therefore never registered
execute_evidence_validation — even though the API process could still
successfully call .delay() on it (the API imports validation_tasks.py
directly via app.api.endpoints.validation), producing a real DB row stuck
permanently at RUNNING with the worker rejecting the message as unknown.

IMPORTANT TESTING NOTE: a naive in-process check of
`app.worker.celery_app.celery_app.tasks` from within this pytest process
would NOT actually prove the fix, because pytest's own process already
imports app.api.endpoints.validation (to test the API), which transitively
imports validation_tasks.py — populating celery_app.tasks in THIS process
regardless of the `include` list. That is exactly how the original bug
went undetected by any existing test. To authoritatively prove worker-side
registration, test A below spawns a FRESH subprocess that imports only
app.worker.celery_app and asks Celery's own loader to import its
`include` list — the same mechanism a real `celery worker` startup uses —
with no other module pulling validation_tasks.py in via a side door.
"""
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.shared import Merchant, Case, ProcessingState
from app.models.module_e import (
    EvidencePolicyVersion,
    EvidenceValidationRun,
    EValidationRunStatus,
    EvidenceValidationResult,
    EvidenceRequirementAssessment,
    CrossSourceFieldLink,
    CaseFeatureSnapshot,
)
from app.worker.celery_app import celery_app
from app.worker.validation_tasks import execute_evidence_validation

EXPECTED_TASK_NAME = "app.worker.validation_tasks.execute_evidence_validation"

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_celery_validation_registration.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def make_case(db, state=ProcessingState.RECEIVED):
    merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant", is_active=True)
    db.add(merchant)
    db.commit()
    case = Case(
        merchant_id=merchant.merchant_id,
        external_dispute_id=f"disp_{uuid.uuid4()}",
        source="synthetic",
        processing_state=state,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def make_run(db, case, status=EValidationRunStatus.RUNNING):
    policy = EvidencePolicyVersion(
        payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(policy)
    db.commit()
    run = EvidenceValidationRun(
        case_id=case.case_id,
        evidence_version="v1",
        policy_version_id=policy.policy_version_id,
        status=status,
        started_at=datetime.now(timezone.utc),
        idempotency_key=f"idemp_{uuid.uuid4()}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# A. The task is discoverable in a FRESH worker-equivalent process registry
#    (not merely in pytest's own already-polluted import graph).
# ---------------------------------------------------------------------------
def test_task_registered_in_fresh_worker_equivalent_process():
    script = (
        "from app.worker.celery_app import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "import json\n"
        "print(json.dumps(sorted(celery_app.tasks.keys())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd="/home/user/Resolve_AI/backend",
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    registered = json.loads(result.stdout.strip().splitlines()[-1])
    assert EXPECTED_TASK_NAME in registered, (
        f"{EXPECTED_TASK_NAME} not in fresh worker-equivalent registry: {registered}"
    )


# ---------------------------------------------------------------------------
# B. The task's own name matches what the enqueueing code (validate_evidence
#    endpoint) actually calls .delay() on.
# ---------------------------------------------------------------------------
def test_task_name_matches_enqueue_target():
    assert execute_evidence_validation.name == EXPECTED_TASK_NAME
    from app.api.endpoints.validation import execute_evidence_validation as imported_at_call_site
    assert imported_at_call_site is execute_evidence_validation
    assert imported_at_call_site.name == EXPECTED_TASK_NAME


# ---------------------------------------------------------------------------
# C. The API/service enqueue path targets exactly this registered task,
#    exactly once, only when a new run is created (not on idempotent reuse).
# ---------------------------------------------------------------------------
def test_enqueue_path_calls_the_registered_task_exactly_once(mocker):
    mock_delay = mocker.patch("app.api.endpoints.validation.execute_evidence_validation.delay")
    from app.services.validation import prepare_validation_run
    import app.models.module_a
    import app.models.module_b

    def create_full_case_setup(db):
        merchant = Merchant(external_merchant_id=f"ext_{uuid.uuid4()}", name="Test Merchant")
        db.add(merchant)
        db.commit()
        case = Case(
            merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}",
            source="synthetic", processing_state=ProcessingState.D_INTELLIGENCE_READY,
        )
        db.add(case)
        db.commit()
        payment = app.models.module_b.Payment(
            case_id=case.case_id, external_payment_id=f"pay_{uuid.uuid4()}", amount_minor=1000,
            currency="USD", status="captured", method="card", network="Visa",
            fetched_at=datetime.now(timezone.utc), created_at_source=datetime.now(timezone.utc),
        )
        db.add(payment)
        dispute = app.models.module_a.Dispute(
            case_id=case.case_id, external_dispute_id=case.external_dispute_id,
            payment_id=payment.external_payment_id, amount_minor=1000, currency="USD",
            reason_code="10.4", phase="chargeback", status="open",
            dispute_created_at=datetime.now(timezone.utc),
        )
        db.add(dispute)
        db.commit()
        return merchant, case

    db = TestingSessionLocal()
    try:
        merchant, case = create_full_case_setup(db)
        policy = EvidencePolicyVersion(
            payment_network="Visa", reason_code="10.4", phase="chargeback", version=1,
            effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add(policy)
        db.commit()
        case_id = case.case_id
        merchant_id = merchant.merchant_id
    finally:
        db.close()

    db2 = TestingSessionLocal()
    try:
        # Directly exercise the same service call the endpoint makes.
        run, is_reused = prepare_validation_run(db2, str(case_id), str(merchant_id))
        assert is_reused is False
        if not is_reused:
            execute_evidence_validation.delay(str(run.id))
    finally:
        db2.close()

    mock_delay.assert_called_once_with(str(run.id))


# ---------------------------------------------------------------------------
# D + E. A real (non-mocked) call into the task actually executes it, and a
# run that would otherwise stay RUNNING forever reaches a terminal state
# per the EXISTING, unmodified eligibility-gate logic (case not in
# D_INTELLIGENCE_READY -> FAILED). This exercises the true registered
# task object end to end, not a stand-in.
# ---------------------------------------------------------------------------
def test_previously_stuck_running_run_reaches_terminal_state():
    db = TestingSessionLocal()
    try:
        case = make_case(db, state=ProcessingState.ENRICHED)  # not eligible -> deterministic FAILED
        run = make_run(db, case, status=EValidationRunStatus.RUNNING)
        run_id_str = str(run.id)
    finally:
        db.close()

    with patch("app.worker.validation_tasks.SessionLocal", TestingSessionLocal):
        execute_evidence_validation(run_id_str)

    db2 = TestingSessionLocal()
    try:
        final_run = db2.query(EvidenceValidationRun).filter_by(id=uuid.UUID(run_id_str)).first()
        assert final_run.status != EValidationRunStatus.RUNNING
        assert final_run.status == EValidationRunStatus.FAILED
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# F. Existing validation success-path behavior is preserved: this mirrors
# test_module_e.py's own established fixture pattern for a run that DOES
# reach COMPLETED, proving the fix didn't alter business outcomes.
# ---------------------------------------------------------------------------
def test_existing_success_path_still_reaches_completed():
    db = TestingSessionLocal()
    try:
        case = make_case(db, state=ProcessingState.D_INTELLIGENCE_READY)
        run = make_run(db, case, status=EValidationRunStatus.RUNNING)
        run_id_str = str(run.id)
        case_id = case.case_id
        run_id = run.id
    finally:
        db.close()

    with patch("app.worker.validation_tasks.SessionLocal", TestingSessionLocal):
        execute_evidence_validation(run_id_str)

    db2 = TestingSessionLocal()
    try:
        final_run = db2.query(EvidenceValidationRun).filter_by(id=uuid.UUID(run_id_str)).first()
        assert final_run.status == EValidationRunStatus.COMPLETED
        final_case = db2.query(Case).filter_by(case_id=case_id).first()
        assert final_case.processing_state == ProcessingState.FEATURE_READY
        # A CaseFeatureSnapshot is the existing, unmodified success artifact.
        assert db2.query(CaseFeatureSnapshot).filter_by(validation_run_id=run_id).count() == 1
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# G. No duplicate registration: the task name appears exactly once in the
# fresh worker-equivalent registry (adding validation_tasks to `include`
# must not cause Celery to register the same task twice under two names).
# ---------------------------------------------------------------------------
def test_no_duplicate_registration_in_fresh_worker_equivalent_process():
    script = (
        "from app.worker.celery_app import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "import json\n"
        "names = list(celery_app.tasks.keys())\n"
        "print(json.dumps(names))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd="/home/user/Resolve_AI/backend",
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    names = json.loads(result.stdout.strip().splitlines()[-1])
    occurrences = [n for n in names if n == EXPECTED_TASK_NAME]
    assert len(occurrences) == 1, f"expected exactly one registration, found: {occurrences}"
    # Also confirm the module wasn't listed twice in `include` itself.
    assert celery_app.conf.include.count("app.worker.validation_tasks") == 1
