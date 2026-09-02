import abc
from typing import Dict, Any, Optional

class DocumentTypeDetectorInterface(abc.ABC):
    @abc.abstractmethod
    def detect_type(self, full_text: str) -> Dict[str, Any]:
        """
        Takes the aggregated document text and returns:
        {
            "detected_document_type": str,
            "confidence": float,
            "evidence": str
        }
        """
        pass

class DeterministicClassifier(DocumentTypeDetectorInterface):
    """
    MVP deterministic classifier based on text heuristics.
    """
    
    def detect_type(self, full_text: str) -> Dict[str, Any]:
        text_lower = full_text.lower()
        
        # Test hooks for low confidence / unknown
        if "sim_unknown" in text_lower:
            return {
                "detected_document_type": "UNKNOWN",
                "confidence": 0.2,
                "evidence": "Simulated unknown type"
            }
            
        if "sim_low_conf" in text_lower:
            return {
                "detected_document_type": "INVOICE",
                "confidence": 0.3,
                "evidence": "Simulated low confidence invoice"
            }
        
        if "invoice" in text_lower or "total:" in text_lower or "bill to" in text_lower:
            return {
                "detected_document_type": "INVOICE",
                "confidence": 0.95,
                "evidence": "Found 'invoice' or 'total' keyword"
            }
            
        if "proof of delivery" in text_lower or "pod" in text_lower or "signed by:" in text_lower:
            return {
                "detected_document_type": "PROOF_OF_DELIVERY",
                "confidence": 0.92,
                "evidence": "Found POD signature/keyword"
            }
            
        if "tracking number" in text_lower or "carrier:" in text_lower:
            return {
                "detected_document_type": "COURIER_TRACKING",
                "confidence": 0.88,
                "evidence": "Found tracking keyword"
            }
            
        return {
            "detected_document_type": "UNKNOWN",
            "confidence": 0.4,
            "evidence": "No deterministic keywords matched"
        }

def evaluate_type_match(expected_type: str, detected_type: str, confidence: float) -> str:
    """
    Determines the type_match_status based on expected vs detected type and confidence.
    """
    if detected_type == "UNKNOWN" or confidence < 0.6:
        return "REVIEW_REQUIRED"
        
    if expected_type == detected_type:
        return "MATCH"
        
    return "MISMATCH"
