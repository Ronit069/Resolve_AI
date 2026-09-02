import pytest
import uuid
import json
import io
import time
import hmac
import hashlib
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import patch
from app.main import app
from app.core.config import settings
from app.models.shared import Case, ProcessingState, AppUser, Merchant, AuditLog
from app.models.module_a import WebhookEvent, Dispute, DisputeEvent
from app.models.module_b import Payment, Order, Shipment, Refund, CustomerHistory, CaseEnrichment
from app.models.module_c import EvidenceDocument, EvidenceType, EvidenceProcessingStatus
from app.models.module_d import CaseDocumentIntelligenceStatus, DocumentProcessingJob, DocumentPage, DocumentExtraction, ExtractedField, DocumentQualityAssessment, DocumentModelVersion
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base, get_db

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_d.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="module")
def setup_db_module():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(autouse=True)
def override_dependency():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def clean_db_e2e(setup_db_module):
    # Minimal cleanup
    from app.models.module_d import DocumentProcessingJob
    setup_db_module.query(DocumentProcessingJob).delete()
    setup_db_module.query(EvidenceDocument).delete()
    setup_db_module.query(Case).delete()
    setup_db_module.query(AppUser).delete()
    setup_db_module.commit()
    yield setup_db_module
    setup_db_module.rollback()

client = TestClient(app)

def generate_signature(payload: str) -> str:
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

@patch('app.services.evidence.storage_client')
@patch('app.services.evidence.scan_evidence_task.delay')
@patch('app.worker.tasks.process_evidence_document_task.delay')
def test_full_e2e_pipeline_a_to_d(mock_process_d, mock_scan_c, mock_storage):
    from app.services.enrichment import enrich_case
    from app.providers.synthetic import get_synthetic_providers
    from app.services.evidence import upload_evidence_to_case
    from app.services.scanner import run_evidence_scan
    import starlette.datastructures
    
    # 1. Ingestion (Module A)
    now = int(time.time())
    disp_id = f"disp_e2e_{uuid.uuid4()}"
    evt_id = f"evt_e2e_{uuid.uuid4()}"
    payload = {
        "entity": "event", "account_id": "acc_123", "event": "dispute.created",
        "contains": ["dispute"], "created_at": now,
        "payload": {
            "dispute": {
                "entity": {
                    "id": disp_id, "payment_id": "pay_e2e_abcd", "amount": 2000,
                    "currency": "USD", "reason_code": "product_not_received",
                    "reason_description": "desc", "phase": "fraud", "status": "open", "created_at": now
                }
            }
        }
    }
    payload_str = json.dumps(payload)
    sig = generate_signature(payload_str)
    headers = {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": evt_id}
    response = client.post("/api/v1/webhooks/razorpay", data=payload_str, headers=headers)
    assert response.status_code == 202, f"Webhook failed: {response.text}"
    assert response.json()["status"] == "success", f"Webhook not successful: {response.text}"

    # 2. Case Creation and Enrichment (Module B)
    with TestingSessionLocal() as db:
        case = db.query(Case).filter_by(external_dispute_id=disp_id).first()
        assert case is not None
        
        user = AppUser(merchant_id=case.merchant_id, email=f"e2e_{uuid.uuid4()}@test.com")
        db.add(user)
        db.commit()
        
        enrich_case(db, str(case.case_id), get_synthetic_providers())
        db.refresh(case)
        
        # 3. Evidence Upload (Module C)
        mock_storage.upload_file.return_value = True
        f_invoice = starlette.datastructures.UploadFile(filename="invoice.pdf", file=io.BytesIO(b"%PDF-1.4\nInvoice Content"))
        doc_invoice = upload_evidence_to_case(db, case.case_id, user.user_id, f_invoice, EvidenceType.INVOICE)
        
        # 4. Malware Scan (Module C) -> transition to READY_FOR_OCR
        run_evidence_scan(db, doc_invoice.document_id)
        db.refresh(doc_invoice)
        assert doc_invoice.processing_status == EvidenceProcessingStatus.READY_FOR_OCR
        
        # 5. D Processing
            # Patch the task's SessionLocal to use the test DB
        with patch('app.worker.tasks.SessionLocal') as mock_session_local, \
             patch('app.services.document_processing.loader.download_and_verify_evidence') as mock_download:
            
            mock_session_local.return_value = db
            import contextlib
            @contextlib.contextmanager
            def dummy_download(*args, **kwargs):
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False) as tf:
                    tf.write(b"%PDF-1.4\nInvoice for $2000")
                    tf.flush()
                    yield tf.name
            
            mock_download.side_effect = dummy_download
            
            # Trigger processing via API (this calls request_document_processing and would normally enqueue the task)
            resp = client.post(f"/api/v1/documents/{doc_invoice.document_id}/process")
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]
            
            db.commit()
            db.expire_all()
            
            job_obj = db.query(DocumentProcessingJob).filter_by(job_id=uuid.UUID(job_id)).first()
            assert job_obj is not None
            assert job_obj.status == "QUEUED"
        

