import pytest
import uuid
import datetime
from sqlalchemy.exc import IntegrityError, ProgrammingError
from pydantic import ValidationError

from app.core.database import Base, get_db
from app.main import app
from app.models.module_d import (
    DocumentProcessingJob, DocumentPage, DocumentExtraction,
    ExtractedField, DocumentQualityAssessment, DocumentModelVersion,
    CaseDocumentIntelligenceStatus
)
from app.models.module_c import EvidenceDocument, EvidenceProcessingStatus
from app.models.shared import Case, Merchant, AppUser
from app.schemas.module_d import DocumentIntelligenceResult

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Setup dedicated SQLite DB for isolated module D tests
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_module_d.db"
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

@pytest.fixture(scope="module")
def setup_db_module():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def clean_db(setup_db_module):
    # Ensure fresh data for tests by cascading deletes from parent tables
    setup_db_module.query(DocumentProcessingJob).delete()
    setup_db_module.query(EvidenceDocument).delete()
    setup_db_module.query(Case).delete()
    setup_db_module.query(Merchant).delete()
    setup_db_module.commit()
    yield setup_db_module
    setup_db_module.rollback()

@pytest.fixture
def mock_context(clean_db):
    merchant = Merchant(merchant_id=uuid.uuid4(), name="Test Merchant", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    clean_db.add(merchant)
    clean_db.commit()

    case = Case(
        case_id=uuid.uuid4(), 
        merchant_id=merchant.merchant_id, 
        external_dispute_id=f"disp_{uuid.uuid4()}", 
        source="synthetic",
        processing_state="EVIDENCE_READY"
    )
    clean_db.add(case)
    clean_db.commit()

    doc = EvidenceDocument(
        document_id=uuid.uuid4(),
        case_id=case.case_id,
        merchant_id=merchant.merchant_id,
        evidence_type="INVOICE",
        object_key=f"private/{uuid.uuid4()}",
        mime_type="application/pdf",
        file_size_bytes=1000,
        sha256=f"hash_{uuid.uuid4()}",
        scan_status="CLEAN",
        processing_status=EvidenceProcessingStatus.READY_FOR_OCR
    )
    clean_db.add(doc)
    clean_db.commit()

    model_ver = DocumentModelVersion(
        model_version_id=uuid.uuid4(),
        component="OCR",
        model_name="PaddleOCR",
        version="2.0",
        config_hash="abc"
    )
    clean_db.add(model_ver)
    clean_db.commit()

    return {"merchant": merchant, "case": case, "document": doc, "model_ver": model_ver, "db": clean_db}

def test_all_tables_exist(mock_context):
    db = mock_context["db"]
    # If this doesn't raise ProgrammingError/OperationalError, tables exist
    db.query(DocumentProcessingJob).first()
    db.query(DocumentPage).first()
    db.query(DocumentExtraction).first()
    db.query(ExtractedField).first()
    db.query(DocumentQualityAssessment).first()
    db.query(DocumentModelVersion).first()
    db.query(CaseDocumentIntelligenceStatus).first()

def test_idempotency_key_uniqueness(mock_context):
    db = mock_context["db"]
    job1 = DocumentProcessingJob(
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        merchant_id=mock_context["merchant"].merchant_id,
        job_type="OCR_AND_EXTRACT",
        status="QUEUED",
        idempotency_key="UNIQUE_IDEMP_1",
        pipeline_version="v1"
    )
    db.add(job1)
    db.commit()

    job2 = DocumentProcessingJob(
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        merchant_id=mock_context["merchant"].merchant_id,
        job_type="OCR_AND_EXTRACT",
        status="QUEUED",
        idempotency_key="UNIQUE_IDEMP_1",  # Duplicate!
        pipeline_version="v1"
    )
    db.add(job2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_page_number_uniqueness(mock_context):
    db = mock_context["db"]
    job = DocumentProcessingJob(
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        merchant_id=mock_context["merchant"].merchant_id,
        job_type="OCR_AND_EXTRACT",
        status="QUEUED",
        idempotency_key=f"IDEMP_{uuid.uuid4()}",
        pipeline_version="v1"
    )
    db.add(job)
    db.commit()

    page1 = DocumentPage(
        job_id=job.job_id,
        document_id=mock_context["document"].document_id,
        page_number=1,
    )
    db.add(page1)
    db.commit()

    page2 = DocumentPage(
        job_id=job.job_id,
        document_id=mock_context["document"].document_id,
        page_number=1,  # Duplicate page number for same job!
    )
    db.add(page2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_extraction_uniqueness(mock_context):
    db = mock_context["db"]
    job = DocumentProcessingJob(
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        merchant_id=mock_context["merchant"].merchant_id,
        job_type="OCR_AND_EXTRACT",
        status="COMPLETED",
        idempotency_key=f"IDEMP_{uuid.uuid4()}",
        pipeline_version="v1"
    )
    db.add(job)
    db.commit()

    ext1 = DocumentExtraction(
        job_id=job.job_id,
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        expected_evidence_type="INVOICE",
        type_match_status="MATCH",
        extraction_status="COMPLETED",
        schema_version="1.0"
    )
    db.add(ext1)
    db.commit()

    ext2 = DocumentExtraction(
        job_id=job.job_id,
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        expected_evidence_type="INVOICE",
        type_match_status="MATCH",
        extraction_status="COMPLETED",
        schema_version="1.0"
    )
    db.add(ext2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_quality_uniqueness(mock_context):
    db = mock_context["db"]
    job = DocumentProcessingJob(
        document_id=mock_context["document"].document_id,
        case_id=mock_context["case"].case_id,
        merchant_id=mock_context["merchant"].merchant_id,
        job_type="OCR_AND_EXTRACT",
        status="COMPLETED",
        idempotency_key=f"IDEMP_{uuid.uuid4()}",
        pipeline_version="v1"
    )
    db.add(job)
    db.commit()

    q1 = DocumentQualityAssessment(
        job_id=job.job_id,
        document_id=mock_context["document"].document_id
    )
    db.add(q1)
    db.commit()

    q2 = DocumentQualityAssessment(
        job_id=job.job_id,
        document_id=mock_context["document"].document_id
    )
    db.add(q2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_schema_contract_serialization():
    # Test valid DTO instantiation
    valid_data = {
        "case_id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "evidence_type": "INVOICE",
        "detected_document_type": "INVOICE",
        "type_match_status": "MATCH",
        "extraction_status": "COMPLETED",
        "schema_version": "1.0",
        "overall_confidence": 0.95,
        "quality": {
            "quality_score": 0.99,
            "quality_grade": "GOOD",
            "flags": []
        },
        "fields": [
            {
                "field_name": "invoice_amount",
                "value_type": "DECIMAL",
                "canonical_value": "100.00",
                "field_confidence": 0.98,
                "review_status": "NOT_REQUIRED"
            }
        ],
        "artifacts": {
            "normalized_text_object_key": "private/ocr_text.txt"
        },
        "pipeline_version": "v1",
        "ready_for_validation": True
    }
    obj = DocumentIntelligenceResult(**valid_data)
    assert obj.overall_confidence == 0.95
    assert obj.fields[0].field_name == "invoice_amount"

    # Test invalid data fails (e.g. missing required field)
    invalid_data = valid_data.copy()
    del invalid_data["evidence_type"]
    with pytest.raises(ValidationError):
        DocumentIntelligenceResult(**invalid_data)

from fastapi.testclient import TestClient

def test_eligibility_clean_document(mock_context):
    client = TestClient(app)
    # The app has a dependency override for the DB, so it uses the isolated test DB
    doc_id = mock_context["document"].document_id
    response = client.post(f"/api/v1/documents/{doc_id}/process")
    assert response.status_code == 202
    assert response.json()["status"] == "ACCEPTED"
    assert "job_id" in response.json()
    assert response.json()["idempotent_reused"] is False

def test_eligibility_idempotency(mock_context):
    client = TestClient(app)
    doc_id = mock_context["document"].document_id
    # First request
    res1 = client.post(f"/api/v1/documents/{doc_id}/process")
    assert res1.status_code == 202
    # Second request
    res2 = client.post(f"/api/v1/documents/{doc_id}/process")
    assert res2.status_code == 202
    assert res2.json()["job_id"] == res1.json()["job_id"]
    assert res2.json()["idempotent_reused"] is True

def test_eligibility_rejects_quarantined(mock_context):
    db = mock_context["db"]
    # Corrupt the document to QUARANTINED
    doc = mock_context["document"]
    doc.scan_status = "INFECTED"
    doc.processing_status = EvidenceProcessingStatus.QUARANTINED
    db.commit()

    client = TestClient(app)
    response = client.post(f"/api/v1/documents/{doc.document_id}/process")
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "DOC_NOT_ELIGIBLE"

    # Restore
    doc.scan_status = "CLEAN"
    doc.processing_status = EvidenceProcessingStatus.READY_FOR_OCR
    db.commit()

def test_tenant_isolation(mock_context):
    db = mock_context["db"]
    # Create another tenant's document
    merchant2 = Merchant(merchant_id=uuid.uuid4(), name="Other", external_merchant_id="other", is_active=False)
    db.add(merchant2)
    db.commit()
    
    doc2 = EvidenceDocument(
        document_id=uuid.uuid4(),
        case_id=mock_context["case"].case_id,
        merchant_id=merchant2.merchant_id,  # belongs to merchant 2
        evidence_type="INVOICE",
        object_key="private/other",
        mime_type="application/pdf",
        file_size_bytes=1000,
        sha256="hash2",
        scan_status="CLEAN",
        processing_status=EvidenceProcessingStatus.READY_FOR_OCR
    )
    db.add(doc2)
    db.commit()

    client = TestClient(app)
    # The dependency override returns the first active merchant (Test Merchant)
    response = client.post(f"/api/v1/documents/{doc2.document_id}/process")
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "OBJECT_NOT_FOUND"

def test_get_processing_status(mock_context):
    client = TestClient(app)
    doc_id = mock_context["document"].document_id
    
    # Process it first
    client.post(f"/api/v1/documents/{doc_id}/process")
    
    # Now get status
    response = client.get(f"/api/v1/documents/{doc_id}/processing")
    assert response.status_code == 200
    assert response.json()["status"] in ["QUEUED", "PROCESSING"]
    assert "job_id" in response.json()
    assert "duration_ms" in response.json()

from unittest.mock import patch, MagicMock
from app.services.document_processing.loader import download_and_verify_evidence
from app.services.intelligence import IntelligenceError
import hashlib
import os
import fitz # PyMuPDF
from PIL import Image
import io

def test_loader_integrity_mismatch():
    with patch("app.core.storage.storage_client.s3_client.get_object") as mock_get:
        # Mock S3 response
        mock_body = MagicMock()
        mock_body.iter_chunks.return_value = [b"fake_data"]
        mock_get.return_value = {"Body": mock_body}
        
        # Expecting hash "abc" but data is "fake_data"
        import pytest
        with pytest.raises(IntelligenceError) as exc_info:
            with download_and_verify_evidence("fake_key", "abc"):
                pass
        
        assert exc_info.value.code == "HASH_MISMATCH"

def test_loader_integrity_match():
    with patch("app.core.storage.storage_client.s3_client.get_object") as mock_get:
        data = b"real_data"
        mock_body = MagicMock()
        mock_body.iter_chunks.return_value = [data]
        mock_get.return_value = {"Body": mock_body}
        
        expected_hash = hashlib.sha256(data).hexdigest()
        
        with download_and_verify_evidence("fake_key", expected_hash) as tmp_path:
            assert os.path.exists(tmp_path)
            with open(tmp_path, "rb") as f:
                assert f.read() == data
                
        # Assert cleanup
        assert not os.path.exists(tmp_path)

def test_pdf_processing(mock_context):
    from app.services.document_processing.pdf import PDFProcessor
    db = mock_context["db"]
    job_id = uuid.uuid4()
    doc_id = mock_context["document"].document_id
    case_id = mock_context["case"].case_id
    
    # Create a simple PDF in memory and write to temp file
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((50, 50), "Test Native Text " * 10) # Enough text to be considered usable
    
    tmp_pdf = "test_temp.pdf"
    pdf.save(tmp_pdf)
    pdf.close()
    
    try:
        pages = PDFProcessor.process_pdf(db, tmp_pdf, job_id, doc_id, case_id)
        assert len(pages) == 1
        assert pages[0].page_number == 1
        assert pages[0].native_text_used is True
        assert pages[0].page_artifact_key is None # No rendering needed
    finally:
        try:
            os.remove(tmp_pdf)
        except Exception:
            pass

def test_image_processing(mock_context):
    from app.services.document_processing.image import ImageProcessor
    db = mock_context["db"]
    job_id = uuid.uuid4()
    doc_id = mock_context["document"].document_id
    case_id = mock_context["case"].case_id
    
    # Create test image
    img = Image.new('RGB', (100, 100), color = 'red')
    tmp_img = "test_temp.png"
    img.save(tmp_img)
    
    with patch("app.core.storage.storage_client.upload_file") as mock_upload:
        try:
            pages = ImageProcessor.process_image(db, tmp_img, job_id, doc_id, case_id, "image/png")
            assert len(pages) == 1
            assert pages[0].width_px == 100
            assert pages[0].height_px == 100
            assert pages[0].page_artifact_key is not None
            mock_upload.assert_called_once()
        finally:
            try:
                os.remove(tmp_img)
            except Exception:
                pass


def test_ocr_adapter_deterministic_valid():
    from app.services.document_processing.ocr_adapter import DeterministicOCRAdapter, validate_ocr_result
    adapter = DeterministicOCRAdapter()
    
    res = adapter.perform_ocr("derived/case/doc/job/page_1.png", 800, 600, 1)
    validate_ocr_result(res, 800, 600)
    
    assert res["page_number"] == 1
    assert "Invoice Number" in res["text"]
    assert res["confidence"] > 0.9
    assert len(res["layout_blocks"]) == 2
    
    block = res["layout_blocks"][0]
    assert block["block_type"] == "text"
    assert len(block["bbox"]) == 4

def test_ocr_adapter_deterministic_invalid_bounds():
    from app.services.document_processing.ocr_adapter import DeterministicOCRAdapter, validate_ocr_result
    from app.services.intelligence import IntelligenceError
    adapter = DeterministicOCRAdapter()
    
    # We can craft an invalid result and test validate_ocr_result directly
    res = {
        "page_number": 1,
        "text": "test",
        "confidence": 1.5, # invalid
        "layout_blocks": [],
        "source_reference": "ref"
    }
    
    import pytest
    with pytest.raises(IntelligenceError) as exc:
        validate_ocr_result(res, 800, 600)
    assert exc.value.code == "OCR_INVALID_OUTPUT"
    
    res["confidence"] = 0.5
    res["layout_blocks"] = [{"bbox": [-10, 0, 100, 100], "confidence": 0.5}] # Negative coord
    with pytest.raises(IntelligenceError) as exc:
        validate_ocr_result(res, 800, 600)
    assert "Negative" in exc.value.message or "Invalid coordinates" in exc.value.message
    
    res["layout_blocks"] = [{"bbox": [0, 0, 1000, 100], "confidence": 0.5}] # Out of bounds (w=800)
    with pytest.raises(IntelligenceError) as exc:
        validate_ocr_result(res, 800, 600)
    assert "Out of bounds" in exc.value.message or "out of page bounds" in exc.value.message

def test_ocr_adapter_deterministic_simulation():
    from app.services.document_processing.ocr_adapter import DeterministicOCRAdapter
    from app.services.intelligence import IntelligenceError
    adapter = DeterministicOCRAdapter()
    
    import pytest
    with pytest.raises(IntelligenceError) as exc:
        adapter.perform_ocr("timeout", 800, 600, 1)
    assert exc.value.code == "OCR_TIMEOUT"
    
    with pytest.raises(IntelligenceError) as exc:
        adapter.perform_ocr("invalid", 800, 600, 1)
    assert exc.value.code == "OCR_INVALID_OUTPUT"
    
    res = adapter.perform_ocr("empty", 800, 600, 1)
    assert res["text"] == ""
    assert len(res["layout_blocks"]) == 0

def test_schema_registry():
    from app.services.document_processing.schema_registry import schema_registry
    
    invoice = schema_registry.get_schema_for_type("INVOICE")
    assert invoice is not None
    assert invoice["schema_name"] == "invoice_schema"
    assert invoice["schema_version"] == "v1.0.0"
    
    tracking = schema_registry.get_schema_for_type("COURIER_TRACKING")
    assert tracking is not None
    
    unknown = schema_registry.get_schema_for_type("UNKNOWN_TYPE")
    assert unknown is None

def test_deterministic_classifier():
    from app.services.document_processing.classifier import DeterministicClassifier, evaluate_type_match
    
    classifier = DeterministicClassifier()
    
    # Test INVOICE
    res = classifier.detect_type("Here is your Invoice Number: 12345")
    assert res["detected_document_type"] == "INVOICE"
    assert res["confidence"] > 0.9
    
    # Test POD
    res = classifier.detect_type("Proof of delivery signed by: John")
    assert res["detected_document_type"] == "PROOF_OF_DELIVERY"
    
    # Test COURIER_TRACKING
    res = classifier.detect_type("Tracking number: 1ZA234")
    assert res["detected_document_type"] == "COURIER_TRACKING"
    
    # Test UNKNOWN
    res = classifier.detect_type("Just a random text document")
    assert res["detected_document_type"] == "UNKNOWN"
    
    # Test MATCH STATUS
    assert evaluate_type_match("INVOICE", "INVOICE", 0.95) == "MATCH"
    assert evaluate_type_match("INVOICE", "PROOF_OF_DELIVERY", 0.92) == "MISMATCH"
    assert evaluate_type_match("INVOICE", "INVOICE", 0.5) == "REVIEW_REQUIRED" # Low conf
    assert evaluate_type_match("INVOICE", "UNKNOWN", 0.4) == "REVIEW_REQUIRED"

def test_extractors():
    from app.services.document_processing.extractor import DeterministicExtractorFactory
    
    # Test INVOICE
    ext = DeterministicExtractorFactory.get_extractor("INVOICE")
    layout = {1: [{"text": "Invoice Number: INV-1001", "bbox": [10, 10, 100, 20]}, {"text": "Total: 500.00", "bbox": [10, 40, 100, 50]}]}
    fields = ext.extract_fields([], layout, {})
    
    assert len(fields) == 2
    inv_num = next(f for f in fields if f.name == "invoice_number")
    assert inv_num.raw_value == "INV-1001"
    assert inv_num.confidence == 0.9
    assert inv_num.provenance.page_number == 1
    assert inv_num.provenance.bbox == [10, 10, 100, 20]
    assert inv_num.provenance.source_text_hash is not None
    
    total = next(f for f in fields if f.name == "total_amount")
    assert total.raw_value == "500.00"
    
    # Test POD
    ext = DeterministicExtractorFactory.get_extractor("PROOF_OF_DELIVERY")
    layout = {2: [{"text": "Signed by: John Doe", "bbox": [0,0,0,0]}]}
    fields = ext.extract_fields([], layout, {})
    assert len(fields) == 1
    assert fields[0].name == "recipient_name"
    assert fields[0].raw_value == "John Doe"
    assert fields[0].provenance.page_number == 2
    
    # Test TRACKING
    ext = DeterministicExtractorFactory.get_extractor("COURIER_TRACKING")
    layout = {1: [{"text": "Tracking number: 1Z999", "bbox": [0,0,0,0]}]}
    fields = ext.extract_fields([], layout, {})
    assert len(fields) == 1
    assert fields[0].name == "tracking_number"
    assert fields[0].raw_value == "1Z999"

def test_extractor_missing_fields():
    from app.services.document_processing.extractor import DeterministicExtractorFactory
    ext = DeterministicExtractorFactory.get_extractor("INVOICE")
    layout = {1: [{"text": "Just some random text", "bbox": [10, 10, 100, 20]}]}
    fields = ext.extract_fields([], layout, {})
    assert len(fields) == 0 # No hallucinated values

def test_extractor_ambiguity_amounts():
    from app.services.document_processing.extractor import DeterministicExtractorFactory
    ext = DeterministicExtractorFactory.get_extractor("INVOICE")
    layout = {1: [
        {"text": "Total: 500.00", "bbox": [10, 40, 100, 50]},
        {"text": "Total: 12500", "bbox": [10, 80, 100, 90]}
    ]}
    fields = ext.extract_fields([], layout, {})
    # Should resolve to 1 amount with review_required=True
    assert len(fields) == 1
    f = fields[0]
    assert f.name == "total_amount"
    assert f.review_required is True
    assert f.confidence == 0.5 # Diminished confidence

def test_field_confidence_bounds():
    from app.services.document_processing.extractor import DeterministicExtractorFactory
    from app.models.module_d import ExtractedField
    
    # Just asserting the sqlalchemy constraints aren't violated on instantiation conceptually,
    # but more importantly that our factory logic bounds them natively.
    ext = DeterministicExtractorFactory.get_extractor("INVOICE")
    layout = {1: [{"text": "Total: 500.00", "bbox": [10, 40, 100, 50]}]}
    fields = ext.extract_fields([], layout, {})
    assert 0.0 <= fields[0].confidence <= 1.0

def test_extractor_multipage_provenance():
    from app.services.document_processing.extractor import DeterministicExtractorFactory
    ext = DeterministicExtractorFactory.get_extractor("INVOICE")
    layout = {
        1: [{"text": "Invoice Number: INV-1001", "bbox": [10, 10, 100, 20]}],
        2: [{"text": "Total: 500.00", "bbox": [10, 40, 100, 50]}]
    }
    fields = ext.extract_fields([], layout, {})
    
    assert len(fields) == 2
    inv_num = next(f for f in fields if f.name == "invoice_number")
    total = next(f for f in fields if f.name == "total_amount")
    
    assert inv_num.provenance.page_number == 1
    assert total.provenance.page_number == 2

def test_normalizer_identifier():
    from app.services.document_processing.normalizer import DeterministicNormalizerRegistry
    from app.models.module_d import ExtractedField
    
    normalizer = DeterministicNormalizerRegistry.get_normalizer("invoice_number")
    f = ExtractedField(field_name="invoice_number", raw_value_masked="  INV-1001  ", review_status="NOT_REQUIRED")
    normalizer.normalize_field(f)
    assert f.canonical_value_text == "INV-1001"
    assert f.value_type == "identifier"
    assert f.raw_value_masked == "  INV-1001  " # preserved

def test_normalizer_date():
    from app.services.document_processing.normalizer import DeterministicNormalizerRegistry
    from app.models.module_d import ExtractedField
    from datetime import datetime
    
    normalizer = DeterministicNormalizerRegistry.get_normalizer("invoice_date")
    
    # ISO
    f1 = ExtractedField(field_name="invoice_date", raw_value_masked="2026-08-20")
    normalizer.normalize_field(f1)
    assert f1.canonical_value_text == "2026-08-20"
    assert f1.date_value == datetime(2026, 8, 20)
    assert f1.value_type == "date"
    
    # Unambiguous
    f2 = ExtractedField(field_name="invoice_date", raw_value_masked="20-Aug-2026")
    normalizer.normalize_field(f2)
    assert f2.canonical_value_text == "2026-08-20"
    
    # Ambiguous
    f3 = ExtractedField(field_name="invoice_date", raw_value_masked="03/04/2026", review_status="NOT_REQUIRED")
    normalizer.normalize_field(f3)
    assert f3.canonical_value_text is None
    assert f3.review_status == "REVIEW_REQUIRED"

def test_normalizer_money():
    from app.services.document_processing.normalizer import DeterministicNormalizerRegistry
    from app.models.module_d import ExtractedField
    from decimal import Decimal
    
    normalizer = DeterministicNormalizerRegistry.get_normalizer("total_amount")
    
    # ₹12,500.00
    f1 = ExtractedField(field_name="total_amount", raw_value_masked="₹12,500.00")
    normalizer.normalize_field(f1)
    assert f1.numeric_value == Decimal("12500.00")
    assert f1.canonical_value_text == "12500.00"
    assert f1.currency_code == "INR"
    assert f1.value_type == "monetary_amount"
    assert f1.raw_value_masked == "₹12,500.00" # Preserved!
    
    # Missing currency
    f2 = ExtractedField(field_name="total_amount", raw_value_masked="12500.00", review_status="NOT_REQUIRED")
    normalizer.normalize_field(f2)
    assert f2.canonical_value_text is None
    assert f2.review_status == "REVIEW_REQUIRED"

def test_normalizer_idempotency():
    from app.services.document_processing.normalizer import DeterministicNormalizerRegistry
    from app.models.module_d import ExtractedField
    from decimal import Decimal
    
    normalizer = DeterministicNormalizerRegistry.get_normalizer("total_amount")
    f = ExtractedField(field_name="total_amount", raw_value_masked="₹12,500.00")
    
    # Run 1
    normalizer.normalize_field(f)
    assert f.numeric_value == Decimal("12500.00")
    
    # Run 2
    normalizer.normalize_field(f)
    assert f.numeric_value == Decimal("12500.00") # Still the same, no cumulative conversion

def test_quality_assessor_good():
    from app.services.document_processing.quality import DeterministicQualityAssessor
    from app.models.module_d import DocumentExtraction, ExtractedField
    
    assessor = DeterministicQualityAssessor()
    
    ext = DocumentExtraction(detected_document_type="INVOICE", overall_confidence=0.95)
    f1 = ExtractedField(field_name="invoice_number", review_status="NOT_REQUIRED", field_confidence=0.9)
    f2 = ExtractedField(field_name="invoice_date", review_status="NOT_REQUIRED", field_confidence=0.9)
    f3 = ExtractedField(field_name="total_amount", review_status="NOT_REQUIRED", field_confidence=0.9)
    
    assessment = assessor.assess_document(
        job_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000001",
        pages=[],
        extraction=ext,
        extracted_fields=[f1, f2, f3],
        text_chunks=["Long enough text to pass coverage checks" * 10]
    )
    
    assert assessment.quality_grade == "GOOD"
    assert 0.8 <= float(assessment.quality_score) <= 1.0
    assert len(assessment.quality_flags) == 0

def test_quality_assessor_review_required():
    from app.services.document_processing.quality import DeterministicQualityAssessor
    from app.models.module_d import DocumentExtraction, ExtractedField
    
    assessor = DeterministicQualityAssessor()
    
    ext = DocumentExtraction(detected_document_type="INVOICE", overall_confidence=0.8)
    # Missing total amount, making expected count short
    f1 = ExtractedField(field_name="invoice_number", review_status="NOT_REQUIRED", field_confidence=0.9)
    f2 = ExtractedField(field_name="invoice_date", review_status="REVIEW_REQUIRED", field_confidence=0.5) # Ambiguous
    
    assessment = assessor.assess_document(
        job_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000001",
        pages=[],
        extraction=ext,
        extracted_fields=[f1, f2],
        text_chunks=["Short"] # triggers empty page + low coverage
    )
    
    assert assessment.quality_grade == "REVIEW_REQUIRED"
    assert float(assessment.quality_score) < 0.5
    assert "MISSING_REQUIRED_FIELDS" in assessment.quality_flags
    assert "AMBIGUOUS_FIELDS" in assessment.quality_flags
    assert "EMPTY_PAGE_DETECTED" in assessment.quality_flags

def test_aggregator_good(clean_db):
    from app.services.document_processing.aggregator import CaseIntelligenceAggregator
    from app.models.module_c import EvidenceDocument, EvidenceProcessingStatus
    from app.models.module_d import CaseDocumentIntelligenceStatus
    import uuid
    
    case_id = uuid.uuid4()
    
    # Mock documents
    doc1 = EvidenceDocument(
        document_id=uuid.uuid4(), case_id=case_id, merchant_id=uuid.uuid4(), 
        processing_status=EvidenceProcessingStatus.EXTRACTED, evidence_type="INVOICE", 
        object_key="mock1.pdf", mime_type="application/pdf", file_size_bytes=100, sha256="hash1"
    )
    doc2 = EvidenceDocument(
        document_id=uuid.uuid4(), case_id=case_id, merchant_id=doc1.merchant_id, 
        processing_status=EvidenceProcessingStatus.EXTRACTED, evidence_type="INVOICE", 
        object_key="mock2.pdf", mime_type="application/pdf", file_size_bytes=100, sha256="hash2"
    )
    
    clean_db.add(doc1)
    clean_db.add(doc2)
    clean_db.commit()
    
    agg = CaseIntelligenceAggregator()
    status = agg.aggregate_case_status(clean_db, case_id)
    clean_db.commit()
    
    assert status.overall_status == "COMPLETED"
    assert status.total_safe_documents == 2
    assert status.ready_for_module_e is True

def test_aggregator_review_required(clean_db):
    from app.services.document_processing.aggregator import CaseIntelligenceAggregator
    from app.models.module_c import EvidenceDocument, EvidenceProcessingStatus
    import uuid
    
    case_id = uuid.uuid4()
    
    doc1 = EvidenceDocument(
        document_id=uuid.uuid4(), case_id=case_id, merchant_id=uuid.uuid4(), 
        processing_status=EvidenceProcessingStatus.EXTRACTED, evidence_type="INVOICE", 
        object_key="mock3.pdf", mime_type="application/pdf", file_size_bytes=100, sha256="hash3"
    )
    doc2 = EvidenceDocument(
        document_id=uuid.uuid4(), case_id=case_id, merchant_id=doc1.merchant_id, 
        processing_status=EvidenceProcessingStatus.REVIEW_REQUIRED, evidence_type="INVOICE", 
        object_key="mock4.pdf", mime_type="application/pdf", file_size_bytes=100, sha256="hash4"
    )
    
    clean_db.add(doc1)
    clean_db.add(doc2)
    clean_db.commit()
    
    agg = CaseIntelligenceAggregator()
    status = agg.aggregate_case_status(clean_db, case_id)
    clean_db.commit()
    
    assert status.overall_status == "REVIEW_REQUIRED"
    assert status.total_safe_documents == 2
    assert status.ready_for_module_e is False
    assert status.review_required_documents == 1

def test_api_case_intelligence_status(clean_db):
    from app.models.shared import Case, Merchant, AppUser
    import uuid
    from app.models.module_d import CaseDocumentIntelligenceStatus
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # We need a merchant
    m = Merchant(merchant_id=uuid.uuid4(), name="Test Merchant", external_merchant_id="ext_test_123")
    clean_db.add(m)
    clean_db.commit()

    # Document-intelligence auth gap fix: the endpoint now requires an
    # authenticated, same-tenant user (see app.api.deps.get_current_merchant).
    u = AppUser(merchant_id=m.merchant_id, email="test_api_case_intelligence_status@merchant.com", is_active=True)
    clean_db.add(u)
    clean_db.commit()

    c = Case(case_id=uuid.uuid4(), merchant_id=m.merchant_id, external_dispute_id="dispute_123", source="API")
    clean_db.add(c)
    clean_db.commit()

    st = CaseDocumentIntelligenceStatus(
        case_id=c.case_id,
        overall_status="REVIEW_REQUIRED",
        total_safe_documents=3,
        processed_documents=3,
        review_required_documents=1,
        ready_for_module_e=False
    )
    clean_db.add(st)
    clean_db.commit()

    response = client.get(
        f"/api/v1/cases/{c.case_id}/document-intelligence",
        headers={"X-User-Id": str(u.user_id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "REVIEW_REQUIRED"
    assert data["total_safe_documents"] == 3
    assert data["review_required_documents"] == 1
    assert data["ready_for_module_e"] is False
