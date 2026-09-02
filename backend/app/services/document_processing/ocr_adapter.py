import abc
import json
import hashlib
from typing import Dict, Any, List

from app.services.intelligence import IntelligenceError

class OCRAdapterInterface(abc.ABC):
    """
    Abstract boundary for OCR engine integration.
    """
    
    @abc.abstractmethod
    def perform_ocr(self, page_artifact_key: str, width: int, height: int, page_number: int) -> dict:
        """
        Perform OCR and return a standard dictionary:
        {
            "page_number": int,
            "text": str,
            "confidence": float (0.0 to 1.0),
            "layout_blocks": [
                {
                    "text": str,
                    "confidence": float,
                    "bbox": [x0, y0, x1, y1], # Absolute coordinates
                    "block_type": str # "text", "table", "image", etc.
                }
            ],
            "source_reference": str
        }
        """
        pass

class DeterministicOCRAdapter(OCRAdapterInterface):
    """
    MVP / Test deterministic OCR implementation.
    Produces predictable layout and text data for downstream testing.
    """
    
    def perform_ocr(self, page_artifact_key: str, width: int, height: int, page_number: int) -> dict:
        if not page_artifact_key:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Missing page artifact key")
            
        # Simulate different scenarios based on the artifact key (for testing flexibility)
        if "empty" in page_artifact_key:
            return {
                "page_number": page_number,
                "text": "",
                "confidence": 0.0,
                "layout_blocks": [],
                "source_reference": page_artifact_key
            }
        
        if "invalid" in page_artifact_key:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Simulated invalid OCR output")

        if "timeout" in page_artifact_key:
            raise IntelligenceError("OCR_TIMEOUT", "Simulated OCR timeout")
            
        # Default high-confidence deterministic result
        text = "Invoice Number: INV-1001\nTotal: 12500.00"
        
        # Ensure bounding boxes are strictly within bounds
        # Example bbox: [x0, y0, x1, y1]
        max_w = max(width, 100)
        max_h = max(height, 100)
        
        blocks = [
            {
                "text": "Invoice Number: INV-1001",
                "confidence": 0.95,
                "bbox": [0, 0, min(200, max_w), min(30, max_h)],
                "block_type": "text"
            },
            {
                "text": "Total: 12500.00",
                "confidence": 0.98,
                "bbox": [0, 40, min(200, max_w), min(70, max_h)],
                "block_type": "text"
            }
        ]
        
        # Low confidence scenario simulation
        if "lowconf" in page_artifact_key:
            for b in blocks:
                b["confidence"] = 0.4
            
        return {
            "page_number": page_number,
            "text": text,
            "confidence": 0.96 if "lowconf" not in page_artifact_key else 0.4,
            "layout_blocks": blocks,
            "source_reference": page_artifact_key
        }

def validate_ocr_result(result: dict, width: int, height: int):
    """
    Validates OCR layout constraints as required by the specification.
    """
    if "text" not in result or "layout_blocks" not in result:
        raise IntelligenceError("OCR_INVALID_OUTPUT", "Missing standard OCR fields")
        
    for block in result.get("layout_blocks", []):
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Invalid bbox format")
            
        x0, y0, x1, y1 = bbox
        
        import math
        for coord in bbox:
            if math.isnan(coord) or math.isinf(coord) or coord < 0:
                raise IntelligenceError("OCR_INVALID_OUTPUT", "Invalid coordinates (NaN, Infinity, or Negative)")
                
        if x0 > x1 or y0 > y1:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Negative dimensions in bbox")
            
        if x1 > width or y1 > height:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Coordinates out of page bounds")
            
        conf = block.get("confidence", 0.0)
        if conf < 0.0 or conf > 1.0:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Block confidence out of bounds (0.0 - 1.0)")
            
    doc_conf = result.get("confidence", 0.0)
    if doc_conf < 0.0 or doc_conf > 1.0:
        raise IntelligenceError("OCR_INVALID_OUTPUT", "Document confidence out of bounds (0.0 - 1.0)")
