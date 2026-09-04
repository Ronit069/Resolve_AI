"""
D-04 regression tests — dispute-ingestion merchant attribution.

Frozen decision under test: incoming Razorpay account_id -> Merchant.external_merchant_id
-> require an ACTIVE match -> use that merchant for Case/Dispute creation. Never
db.query(Merchant).first(), never a default/demo merchant, never a client-supplied
merchant selector. Unknown or inactive account_id must fail closed: no Case/Dispute
is created and no merchant is attributed.
"""
import pytest
import json
import time
import hmac
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import Base, get_db
from app.core.config import settings
from app.models.module_a import WebhookEvent, Dispute
from app.models.shared import Case, Merchant
from app.services.ingestion import process_dispute_event

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_a_merchant_resolution.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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


def _sign(payload_str: str) -> str:
    return hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _dispute_data(ext_id, payment_id="pay_x", amount=1000, reason_code="fraud", status="open"):
    now = int(time.time())
    return {
        "id": ext_id,
        "payment_id": payment_id,
        "amount": amount,
        "currency": "INR",
        "reason_code": reason_code,
        "status": status,
        "phase": "pre",
        "created_at": now,
        "respond_by": now + 86400,
    }


def _webhook_payload(account_id, ext_id, event_id):
    dd = _dispute_data(ext_id)
    payload = {
        "entity": "event",
        "account_id": account_id,
        "event": "dispute.created",
        "contains": ["dispute"],
        "payload": {"dispute": {"entity": {**dd, "reason_description": "desc"}}},
        "created_at": dd["created_at"],
    }
    payload_str = json.dumps(payload)
    sig = _sign(payload_str)
    headers = {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id}
    return payload_str, headers


# ---------------------------------------------------------------------------
# TEST 1: two merchants, correct account_id -> merchant mapping, both directions.
# The second-created merchant's event is processed FIRST, so a db.query(Merchant)
# .first() implementation (which would always return the first-inserted row)
# cannot coincidentally produce the correct answer here.
# ---------------------------------------------------------------------------
def test_correct_merchant_mapping_both_directions(mocker):
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    db = TestingSessionLocal()
    try:
        merchant_a = Merchant(external_merchant_id="acc_alpha", name="Alpha Merchant", is_active=True)
        db.add(merchant_a)
        merchant_b = Merchant(external_merchant_id="acc_beta", name="Beta Merchant", is_active=True)
        db.add(merchant_b)
        db.commit()
        db.refresh(merchant_a)
        db.refresh(merchant_b)

        # Process merchant_b's event first, even though merchant_a was inserted
        # first (lower primary-key/insertion order) — a .first() bug would
        # misattribute this to merchant_a.
        raw_b = json.dumps(_dispute_data("disp_beta_1")).encode()
        case_id_b = process_dispute_event(
            db=db, source="synthetic", raw_payload=raw_b,
            event_id="evt_beta_1", event_type="dispute.created",
            event_time=datetime.now(timezone.utc), dispute_data=_dispute_data("disp_beta_1"),
            account_id="acc_beta",
        )
        raw_a = json.dumps(_dispute_data("disp_alpha_1")).encode()
        case_id_a = process_dispute_event(
            db=db, source="synthetic", raw_payload=raw_a,
            event_id="evt_alpha_1", event_type="dispute.created",
            event_time=datetime.now(timezone.utc), dispute_data=_dispute_data("disp_alpha_1"),
            account_id="acc_alpha",
        )

        case_b = db.query(Case).filter(Case.case_id == uuid.UUID(case_id_b)).first()
        case_a = db.query(Case).filter(Case.case_id == uuid.UUID(case_id_a)).first()
        assert case_b.merchant_id == merchant_b.merchant_id
        assert case_a.merchant_id == merchant_a.merchant_id
        assert case_b.merchant_id != case_a.merchant_id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# TEST 2: unknown account_id -> fail closed. No Case/Dispute is created, and no
# merchant is attributed to an unrelated tenant.
# ---------------------------------------------------------------------------
def test_unknown_account_id_fails_closed(mocker):
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    db = TestingSessionLocal()
    try:
        # A real, unrelated, active merchant exists — proving the rejection
        # is not merely "no merchants in the DB at all".
        db.add(Merchant(external_merchant_id="acc_real", name="Real Merchant", is_active=True))
        db.commit()

        dd = _dispute_data("disp_unknown_acc")
        with pytest.raises(HTTPException) as exc_info:
            process_dispute_event(
                db=db, source="synthetic", raw_payload=json.dumps(dd).encode(),
                event_id="evt_unknown_acc", event_type="dispute.created",
                event_time=datetime.now(timezone.utc), dispute_data=dd,
                account_id="acc_does_not_exist",
            )
        assert exc_info.value.status_code == 400

        assert db.query(Case).count() == 0
        assert db.query(Dispute).count() == 0
        # The WebhookEvent insert itself is rolled back along with everything else.
        assert db.query(WebhookEvent).filter(WebhookEvent.external_event_id == "evt_unknown_acc").first() is None
    finally:
        db.close()


def test_unknown_account_id_fails_closed_via_http(mocker):
    """Same scenario through the real HTTP webhook endpoint."""
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    db = TestingSessionLocal()
    db.add(Merchant(external_merchant_id="acc_real", name="Real Merchant", is_active=True))
    db.commit()
    db.close()

    payload_str, headers = _webhook_payload("acc_totally_unknown", "disp_http_unknown", "evt_http_unknown")
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 400

    db = TestingSessionLocal()
    try:
        assert db.query(Case).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# TEST 3: inactive merchant's own account_id -> fail closed, with no fallback
# to a different, active merchant.
# ---------------------------------------------------------------------------
def test_inactive_merchant_fails_closed_no_fallback(mocker):
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    db = TestingSessionLocal()
    try:
        inactive = Merchant(external_merchant_id="acc_inactive", name="Inactive Merchant", is_active=False)
        db.add(inactive)
        # A different, active merchant also exists — proving the rejected
        # event is not silently reattributed to it.
        other_active = Merchant(external_merchant_id="acc_other_active", name="Other Active Merchant", is_active=True)
        db.add(other_active)
        db.commit()

        dd = _dispute_data("disp_inactive_acc")
        with pytest.raises(HTTPException) as exc_info:
            process_dispute_event(
                db=db, source="synthetic", raw_payload=json.dumps(dd).encode(),
                event_id="evt_inactive_acc", event_type="dispute.created",
                event_time=datetime.now(timezone.utc), dispute_data=dd,
                account_id="acc_inactive",
            )
        assert exc_info.value.status_code == 400

        assert db.query(Case).count() == 0
        assert db.query(Dispute).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# TEST 4: full webhook end-to-end via the real HTTP endpoint with valid HMAC
# signing, for two distinct merchants.
# ---------------------------------------------------------------------------
def test_webhook_end_to_end_http_two_merchants(mocker):
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    db = TestingSessionLocal()
    merchant_x = Merchant(external_merchant_id="acc_x", name="Merchant X", is_active=True)
    db.add(merchant_x)
    merchant_y = Merchant(external_merchant_id="acc_y", name="Merchant Y", is_active=True)
    db.add(merchant_y)
    db.commit()
    db.refresh(merchant_x)
    db.refresh(merchant_y)
    db.close()

    payload_str_x, headers_x = _webhook_payload("acc_x", "disp_http_x", "evt_http_x")
    r_x = client.post("/api/v1/webhooks/razorpay", data=payload_str_x, headers=headers_x)
    assert r_x.status_code == 202

    payload_str_y, headers_y = _webhook_payload("acc_y", "disp_http_y", "evt_http_y")
    r_y = client.post("/api/v1/webhooks/razorpay", data=payload_str_y, headers=headers_y)
    assert r_y.status_code == 202

    db = TestingSessionLocal()
    try:
        case_x = db.query(Case).filter(Case.case_id == uuid.UUID(r_x.json()["case_id"])).first()
        case_y = db.query(Case).filter(Case.case_id == uuid.UUID(r_y.json()["case_id"])).first()
        assert case_x.merchant_id == merchant_x.merchant_id
        assert case_y.merchant_id == merchant_y.merchant_id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# TEST 5: idempotency regression — replaying the same event_id must not create
# a duplicate Case/Dispute, and must not fail merchant resolution differently
# on replay.
# ---------------------------------------------------------------------------
def test_idempotency_replay_no_duplicate(mocker):
    mocker.patch("app.services.ingestion.enrich_dispute_task.delay")
    db = TestingSessionLocal()
    db.add(Merchant(external_merchant_id="acc_idem", name="Idempotency Merchant", is_active=True))
    db.commit()
    db.close()

    payload_str, headers = _webhook_payload("acc_idem", "disp_idem", "evt_idem")

    r1 = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert r1.status_code == 202

    r2 = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert r2.status_code == 202  # graceful no-op, same as pre-existing idempotency behavior

    db = TestingSessionLocal()
    try:
        assert db.query(Case).count() == 1
        assert db.query(Dispute).count() == 1
        assert db.query(WebhookEvent).filter(WebhookEvent.external_event_id == "evt_idem").count() == 1
    finally:
        db.close()
