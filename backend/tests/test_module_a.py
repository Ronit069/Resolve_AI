import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import hmac
import hashlib
import json
import time
from datetime import datetime, timezone, timedelta

from app.main import app
from app.core.database import Base, get_db
from app.core.config import settings
from app.models.module_a import WebhookEvent, Dispute, DisputeEvent
from app.models.shared import Case, ProcessingState

# Use SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(autouse=True)
def setup_overrides():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    
def generate_signature(payload_str: str) -> str:
    return hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def test_razorpay_webhook_valid_signature(mocker):
    # Mock celery task
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_valid1",
                    "payment_id": "pay_valid1",
                    "amount": 1000,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "fraud description",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                    "respond_by": int(time.time()) + 86400
                }
            }
        },
        "created_at": int(time.time())
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_valid1"
    }
    
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 202
    
    db = TestingSessionLocal()
    case = db.query(Case).first()
    assert case is not None
    assert case.processing_state == ProcessingState.INGESTED
    
    disp = db.query(Dispute).filter(Dispute.case_id == case.case_id).first()
    assert disp is not None
    assert disp.external_dispute_id == "disp_valid1"
    
    we = db.query(WebhookEvent).filter(WebhookEvent.external_event_id == "evt_valid1").first()
    assert we is not None
    assert we.signature_verified is True
    
    de = db.query(DisputeEvent).first()
    assert de is not None
    assert de.accepted_transition is True
    
    mock_delay.assert_called_once_with(str(case.case_id))

def test_razorpay_webhook_invalid_signature(mocker):
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    payload = {"some": "data"}
    payload_str = json.dumps(payload)
    
    headers = {
        "X-Razorpay-Signature": "invalid_sig",
        "X-Razorpay-Event-Id": "evt_invalid1"
    }
    
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 401
    
    db = TestingSessionLocal()
    assert db.query(Case).count() == 0
    assert db.query(WebhookEvent).count() == 0
    
    mock_delay.assert_not_called()

def test_razorpay_webhook_idempotency_exact_duplicate(mocker):
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_dup",
                    "payment_id": "pay_dup",
                    "amount": 1000,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "desc",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time())
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_dup"
    }
    
    # First request
    r1 = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert r1.status_code == 202
    assert mock_delay.call_count == 1
    
    # Second request (duplicate)
    r2 = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert r2.status_code == 202 # Graceful success
    
    # Still only 1 call to Celery, 1 Case, 1 WebhookEvent
    assert mock_delay.call_count == 1
    db = TestingSessionLocal()
    assert db.query(Case).count() == 1
    assert db.query(WebhookEvent).count() == 1

def test_stale_event_handling(mocker):
    # Multiple different events for the same dispute.
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    now = int(time.time())
    
    def send_event(evt_id, ts, status):
        payload = {
            "entity": "event",
            "account_id": "acc_123",
            "event": "dispute.updated",
            "contains": ["dispute"],
            "payload": {
                "dispute": {
                    "entity": {
                        "id": "disp_stale_test",
                        "payment_id": "pay_123",
                        "amount": 1000,
                        "currency": "INR",
                        "reason_code": "fraud",
                        "reason_description": "desc",
                        "phase": "fraud",
                        "status": status,
                        "created_at": ts,
                    }
                }
            },
            "created_at": ts
        }
        payload_str = json.dumps(payload)
        sig = generate_signature(payload_str)
        headers = {
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": evt_id
        }
        return client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)

    # 1. Send current event (ts = now)
    r1 = send_event("evt_now", now, "under_review")
    assert r1.status_code == 202
    
    # 2. Send stale event (ts = now - 1000)
    r2 = send_event("evt_old", now - 1000, "open")
    assert r2.status_code == 202
    
    db = TestingSessionLocal()
    # Dispute status should STILL be "under_review", not "open"
    disp = db.query(Dispute).filter(Dispute.external_dispute_id == "disp_stale_test").first()
    assert disp.status == "under_review"
    
    # But a DisputeEvent should exist for the old event with accepted_transition=False
    de_stale = db.query(DisputeEvent).filter(DisputeEvent.external_event_id == "evt_old").first()
    assert de_stale is not None
    assert de_stale.accepted_transition is False
    assert de_stale.new_status == "open"
    
    assert mock_delay.call_count == 2

def test_dev_endpoint_disabled(mocker):
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    # Temporarily disable
    settings.ENABLE_DEV_ENDPOINTS = False
    
    payload = {
        "external_event_id": "dev_1",
        "event_type": "dispute.created",
        "external_dispute_id": "disp_dev",
        "payment_id": "pay_dev",
        "amount_minor": 500,
        "currency": "INR",
        "reason_code": "fraud",
        "status": "open",
        "phase": "fraud",
        "dispute_created_at": datetime.now(timezone.utc).isoformat(),
        "event_time": datetime.now(timezone.utc).isoformat()
    }
    
    r = client.post("/api/v1/dev/disputes", json=payload)
    assert r.status_code == 403
    
    settings.ENABLE_DEV_ENDPOINTS = True
    r = client.post("/api/v1/dev/disputes", json=payload)
    assert r.status_code == 202


def test_invalid_schema_rejected(mocker):
    """A-04: missing payment_id causes 400, no business mutation."""
    mock_delay = mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_schema_bad",
                    # payment_id intentionally omitted -> RazorpayDisputeEntity will fail validation
                    "amount": 1000,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "desc",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time())
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_schema_bad"
    }
    
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    # Pydantic validation error raises 400 / 422
    assert response.status_code in (400, 422)
    
    db = TestingSessionLocal()
    assert db.query(Case).count() == 0
    mock_delay.assert_not_called()


def test_audit_log_created(mocker):
    """A-10: an audit log entry is persisted for every successful ingestion."""
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    from app.models.shared import AuditLog
    
    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_audit",
                    "payment_id": "pay_audit",
                    "amount": 2000,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "desc",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time())
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_audit"
    }
    
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 202
    
    db = TestingSessionLocal()
    logs = db.query(AuditLog).all()
    assert len(logs) == 1
    assert logs[0].action == "DISPUTE_EVENT_INGESTED"
    assert "evt_audit" in logs[0].details


def test_celery_dispatch_failure_does_not_rollback(mocker):
    """
    A-09 / Celery safety: if Celery dispatch fails after a successful DB commit,
    the committed business data must NOT be rolled back. The case remains retryable.
    """
    # Make the delay() call raise an exception
    mock_delay = mocker.patch(
        "app.services.ingestion.enrich_dispute_task.delay",
        side_effect=Exception("Redis connection refused")
    )
    
    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_celery_fail",
                    "payment_id": "pay_celery_fail",
                    "amount": 500,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "desc",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time())
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_celery_fail"
    }
    
    # The endpoint should still return 202; Celery failure is non-fatal for the HTTP response
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 202
    
    # Business data must be committed despite the Celery failure
    db = TestingSessionLocal()
    case = db.query(Case).first()
    assert case is not None, "Case must be committed even when Celery dispatch fails"
    assert case.processing_state == ProcessingState.INGESTED
    
    disp = db.query(Dispute).filter(Dispute.external_dispute_id == "disp_celery_fail").first()
    assert disp is not None, "Dispute must be committed even when Celery dispatch fails"
    
    we = db.query(WebhookEvent).filter(WebhookEvent.external_event_id == "evt_celery_fail").first()
    assert we is not None
    assert we.status == "PROCESSED"


def test_db_unique_constraint_external_event_id():
    """
    Database-level uniqueness: inserting a duplicate external_event_id
    directly into the DB must raise an IntegrityError.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError
    from datetime import datetime, timezone
    
    db = TestingSessionLocal()
    try:
        we1 = WebhookEvent(
            external_event_id="evt_unique_test",
            event_type="dispute.created",
            source="razorpay",
            payload_hash="a" * 64,
            signature_verified=True,
            received_at=datetime.now(timezone.utc),
            status="RECEIVED"
        )
        db.add(we1)
        db.commit()
        
        we2 = WebhookEvent(
            external_event_id="evt_unique_test",  # same ID
            event_type="dispute.created",
            source="razorpay",
            payload_hash="b" * 64,
            signature_verified=True,
            received_at=datetime.now(timezone.utc),
            status="RECEIVED"
        )
        db.add(we2)
        with pytest.raises(SAIntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_state_progression_ingested(mocker):
    """
    A-05 / State machine: a new dispute event must land the case in INGESTED state,
    not RECEIVED or VALIDATED.
    """
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    
    payload = {
        "entity": "event",
        "account_id": "acc_123",
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {
            "dispute": {
                "entity": {
                    "id": "disp_state",
                    "payment_id": "pay_state",
                    "amount": 300,
                    "currency": "INR",
                    "reason_code": "fraud",
                    "reason_description": "desc",
                    "phase": "fraud",
                    "status": "open",
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time())
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": "evt_state"
    }
    
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 202
    
    db = TestingSessionLocal()
    case = db.query(Case).first()
    assert case is not None
    # Must be INGESTED, never RECEIVED or VALIDATED
    assert case.processing_state == ProcessingState.INGESTED
    assert case.processing_state not in (ProcessingState.RECEIVED, ProcessingState.VALIDATED)


def test_multiple_events_same_dispute_one_case(mocker):
    """
    A-03 / A-09: multiple distinct events for the same external dispute_id
    must produce exactly one Case and one Dispute record, but separate DisputeEvent records.
    """
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    now = int(time.time())
    
    def send_event(evt_id, ts, status):
        payload = {
            "entity": "event",
            "account_id": "acc_123",
            "event": "dispute.updated",
            "contains": ["dispute"],
            "payload": {
                "dispute": {
                    "entity": {
                        "id": "disp_multi",
                        "payment_id": "pay_multi",
                        "amount": 1000,
                        "currency": "INR",
                        "reason_code": "fraud",
                        "reason_description": "desc",
                        "phase": "fraud",
                        "status": status,
                        "created_at": ts,
                    }
                }
            },
            "created_at": ts
        }
        payload_str = json.dumps(payload)
        sig = generate_signature(payload_str)
        headers = {
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": evt_id
        }
        return client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)

    r1 = send_event("evt_multi_1", now, "open")
    assert r1.status_code == 202
    r2 = send_event("evt_multi_2", now + 100, "under_review")
    assert r2.status_code == 202
    r3 = send_event("evt_multi_3", now + 200, "resolved")
    assert r3.status_code == 202

    db = TestingSessionLocal()
    # Exactly one Case and one Dispute
    assert db.query(Case).count() == 1
    assert db.query(Dispute).count() == 1
    # Three distinct DisputeEvent records
    assert db.query(DisputeEvent).count() == 3
    # Three distinct WebhookEvent records
    assert db.query(WebhookEvent).count() == 3
    # Final dispute status is the most recent event
    disp = db.query(Dispute).first()
    assert disp.status == "resolved"

