import logging
import hashlib
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.models.module_a import WebhookEvent, Dispute, DisputeEvent
from app.models.shared import Case, ProcessingState, AuditLog, Merchant
from app.schemas.module_a import RazorpayWebhookEvent, SyntheticDisputePayload
from app.worker.tasks import enrich_dispute_task
import uuid

logger = logging.getLogger(__name__)

def process_dispute_event(
    db: Session,
    source: str,
    raw_payload: bytes,
    event_id: str,
    event_type: str,
    event_time: datetime,
    dispute_data: dict,
    account_id: str
) -> str:
    """
    Core logic to ingest a dispute event from Razorpay or Synthetic endpoints.
    Uses exactly the logic requested by the user, isolating event logic from dispute logic,
    handling staleness, idempotency, and dispatching celery task only after commit.
    """
    
    # 1. Idempotency Check: Insert WebhookEvent and handle duplicates
    payload_hash = hashlib.sha256(raw_payload).hexdigest()
    
    webhook_event = WebhookEvent(
        external_event_id=event_id,
        event_type=event_type,
        source=source,
        payload_hash=payload_hash,
        signature_verified=(source == "razorpay"),  # Synthetic is dev only, bypassed signature
        received_at=datetime.now(timezone.utc),
        status="RECEIVED"
    )
    db.add(webhook_event)
    
    try:
        db.flush()
    except IntegrityError:
        # Duplicate external_event_id — UNIQUE constraint fires.
        # Rollback cleanly and return None so the endpoint can issue a graceful 202.
        db.rollback()
        logger.info(f"Duplicate webhook event ignored (idempotency): {event_id}")
        return None

    # 2. Extract dispute fields
    ext_dispute_id = dispute_data.get("id") or dispute_data.get("external_dispute_id")
    payment_id = dispute_data.get("payment_id")
    amount = dispute_data.get("amount") or dispute_data.get("amount_minor")
    currency = dispute_data.get("currency")
    reason_code = dispute_data.get("reason_code") or dispute_data.get("reason")
    status = dispute_data.get("status")
    phase = dispute_data.get("phase")
    dispute_created_at_ts = dispute_data.get("created_at") or dispute_data.get("dispute_created_at")
    
    if isinstance(dispute_created_at_ts, int):
        dispute_created_at = datetime.fromtimestamp(dispute_created_at_ts, timezone.utc)
    else:
        dispute_created_at = dispute_created_at_ts
        
    respond_by_ts = dispute_data.get("respond_by")
    if isinstance(respond_by_ts, int):
        respond_by = datetime.fromtimestamp(respond_by_ts, timezone.utc)
    else:
        respond_by = respond_by_ts

    if not all([ext_dispute_id, payment_id, amount, currency, reason_code, status]):
        logger.error(f"Missing required dispute fields in event {event_id}")
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid dispute payload structure")

    # Resolve the merchant strictly from the event's own account_id. Fail
    # closed: never fall back to an arbitrary/default/first merchant, and
    # never attribute an event to an inactive merchant.
    merchant = db.query(Merchant).filter(
        Merchant.external_merchant_id == account_id,
        Merchant.is_active == True
    ).first()
    if not merchant:
        logger.error(f"Unknown or inactive merchant for account_id={account_id!r} in event {event_id}")
        db.rollback()
        raise HTTPException(status_code=400, detail="Unknown or inactive merchant account")

    # 3. Resolve Case & Dispute
    existing_dispute = db.query(Dispute).filter(Dispute.external_dispute_id == ext_dispute_id).first()
    
    if existing_dispute:
        # Case already exists
        case = db.query(Case).filter(Case.case_id == existing_dispute.case_id).first()
        webhook_event.case_id = case.case_id
        
        # Check staleness
        is_stale = False
        existing_source_updated_at = existing_dispute.source_updated_at
        if existing_source_updated_at and existing_source_updated_at.tzinfo is None:
            existing_source_updated_at = existing_source_updated_at.replace(tzinfo=timezone.utc)
            
        if existing_source_updated_at and existing_source_updated_at > event_time:
            is_stale = True
            logger.info(f"Stale event {event_id} for dispute {ext_dispute_id}. Ignoring status update.")
            
        old_status = existing_dispute.status
        accepted_transition = not is_stale
        
        if accepted_transition:
            existing_dispute.status = status
            existing_dispute.phase = phase
            existing_dispute.source_updated_at = event_time
            # Keep processing_state at least INGESTED (no regress)
            if case.processing_state in [ProcessingState.RECEIVED, ProcessingState.VALIDATED]:
                case.processing_state = ProcessingState.INGESTED
                
        # Create DisputeEvent
        dispute_event = DisputeEvent(
            case_id=case.case_id,
            external_event_id=event_id,
            old_status=old_status,
            new_status=status,
            event_time=event_time,
            accepted_transition=accepted_transition,
            reason="Stale event" if is_stale else "Update accepted"
        )
        db.add(dispute_event)
        
    else:
        # New Case
        case = Case(
            merchant_id=merchant.merchant_id,
            external_dispute_id=ext_dispute_id,
            source=source,
            processing_state=ProcessingState.INGESTED
        )
        db.add(case)
        db.flush()
        
        webhook_event.case_id = case.case_id
        
        dispute = Dispute(
            case_id=case.case_id,
            external_dispute_id=ext_dispute_id,
            payment_id=payment_id,
            amount_minor=amount,
            currency=currency,
            reason_code=reason_code,
            phase=phase,
            status=status,
            dispute_created_at=dispute_created_at,
            respond_by=respond_by,
            source_updated_at=event_time
        )
        db.add(dispute)
        db.flush()
        
        dispute_event = DisputeEvent(
            case_id=case.case_id,
            external_event_id=event_id,
            old_status=None,
            new_status=status,
            event_time=event_time,
            accepted_transition=True,
            reason="Initial dispute creation"
        )
        db.add(dispute_event)

    # 4. Update webhook status
    webhook_event.processed_at = datetime.now(timezone.utc)
    webhook_event.status = "PROCESSED"
    
    # 5. Audit Log
    audit = AuditLog(
        case_id=case.case_id,
        action="DISPUTE_EVENT_INGESTED",
        details=f"Processed event {event_id} of type {event_type} for dispute {ext_dispute_id}"
    )
    db.add(audit)
    
    # 6. Database Commit
    db.commit()
    logger.info(f"Successfully committed event {event_id} for case {case.case_id}")
    
    # 7. Celery Dispatch (ONLY AFTER COMMIT)
    try:
        enrich_dispute_task.delay(str(case.case_id))
        logger.info(f"Dispatched enrich_dispute_task for case {case.case_id}")
    except Exception as e:
        logger.error(f"Failed to dispatch Celery task for case {case.case_id}: {e}")
        # We do not rollback DB. The case is safely retryable later.
        
    return str(case.case_id)
