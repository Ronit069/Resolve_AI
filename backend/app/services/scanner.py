import uuid
from typing import Optional
from sqlalchemy.orm import Session
from app.models.module_c import EvidenceDocument, MalwareScanResult, ScanStatus, EvidenceProcessingStatus
from app.services.requirements import evaluate_case_evidence_coverage

class DeterministicScanner:
    """
    A deterministic malware scanner for MVP and testing.
    Determines outcome based on predefined signatures (hashes or file sizes/metadata).
    Instead of using filenames, we rely on the SHA-256 hash or specific injected bytes 
    (for tests, we can use specific known hashes to trigger INFECTED or FAILED).
    """
    
    # Pre-defined known hashes for testing
    KNOWN_INFECTED_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" # Example hash
    KNOWN_FAILED_HASH = "8a3424d57a2cbcc83d26ff8c4a004ec9a25b29dbf7c164a6ea237a3c3325e2e8"
    
    def scan(self, document: EvidenceDocument) -> MalwareScanResult:
        result = MalwareScanResult(
            document_id=document.document_id,
            scanner="DeterministicScanner",
            scanner_version="1.0"
        )
        
        if document.sha256 == self.KNOWN_INFECTED_HASH:
            result.scan_status = ScanStatus.INFECTED
            result.signature_name = "EICAR-Test-Signature"
        elif document.sha256 == self.KNOWN_FAILED_HASH:
            result.scan_status = ScanStatus.FAILED
        else:
            result.scan_status = ScanStatus.CLEAN
            
        return result

def run_evidence_scan(db: Session, document_id: uuid.UUID) -> Optional[MalwareScanResult]:
    document = db.query(EvidenceDocument).filter(EvidenceDocument.document_id == document_id).first()
    if not document:
        return None
        
    if document.processing_status != EvidenceProcessingStatus.QUARANTINED:
        return None # Only scan quarantined files
        
    scanner = DeterministicScanner()
    scan_result = scanner.scan(document)
    
    db.add(scan_result)
    
    # Update document status based on scan result
    document.scan_status = scan_result.scan_status
    if scan_result.scan_status == ScanStatus.CLEAN:
        document.processing_status = EvidenceProcessingStatus.READY_FOR_OCR
    elif scan_result.scan_status == ScanStatus.INFECTED:
        document.processing_status = EvidenceProcessingStatus.REJECTED
    elif scan_result.scan_status == ScanStatus.FAILED:
        document.processing_status = EvidenceProcessingStatus.SCAN_FAILED
        
    db.commit()
    
    # If it became READY_FOR_OCR, re-evaluate coverage for the case
    if document.processing_status == EvidenceProcessingStatus.READY_FOR_OCR:
        evaluate_case_evidence_coverage(db, document.case_id)
        
    return scan_result
