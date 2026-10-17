import abc
import io
import json
import hashlib
import logging
from typing import Dict, Any, List

from app.services.intelligence import IntelligenceError

logger = logging.getLogger(__name__)

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

class TesseractOCRAdapter(OCRAdapterInterface):
    """
    Real OCR implementation backed by the Tesseract engine (via pytesseract).

    Downloads the already-preprocessed page artifact that ImageProcessor/
    PDFProcessor uploaded to object storage (page_artifact_key), runs
    Tesseract's word-level recognition, and groups words into per-line
    layout_blocks matching the same {text, confidence, bbox, block_type}
    contract DeterministicOCRAdapter produces. Requires the tesseract-ocr
    system package to be installed (see requirements.txt / Dockerfiles).
    """

    def __init__(self, timeout_seconds: int = 30):
        self.timeout_seconds = timeout_seconds

    def perform_ocr(self, page_artifact_key: str, width: int, height: int, page_number: int) -> dict:
        if not page_artifact_key:
            raise IntelligenceError("OCR_INVALID_OUTPUT", "Missing page artifact key")

        from app.core.storage import storage_client

        try:
            raw_bytes = storage_client.download_file(page_artifact_key)
        except Exception as e:
            raise IntelligenceError("OCR_INVALID_OUTPUT", f"Could not read page artifact {page_artifact_key}: {e}") from e

        try:
            import pytesseract
            from pytesseract import Output
            from PIL import Image

            with Image.open(io.BytesIO(raw_bytes)) as img:
                img.load()
                data = pytesseract.image_to_data(
                    img, output_type=Output.DICT, timeout=self.timeout_seconds
                )
        except RuntimeError as e:
            raise IntelligenceError("OCR_TIMEOUT", f"Tesseract timed out on {page_artifact_key}: {e}") from e
        except IntelligenceError:
            raise
        except Exception as e:
            raise IntelligenceError("OCR_INVALID_OUTPUT", f"Tesseract OCR failed on {page_artifact_key}: {e}") from e

        return self._to_standard_result(data, width, height, page_number, page_artifact_key)

    @staticmethod
    def _to_standard_result(data: Dict[str, list], width: int, height: int, page_number: int, source_reference: str) -> dict:
        # Group Tesseract's word-level rows (level 5) into per-line blocks.
        lines: Dict[tuple, Dict[str, Any]] = {}
        n = len(data.get("text", []))

        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError, KeyError):
                continue
            if conf < 0:  # Tesseract marks non-text rows with conf == -1
                continue

            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            left, top = data["left"][i], data["top"][i]
            right, bottom = left + data["width"][i], top + data["height"][i]

            line = lines.setdefault(key, {
                "words": [], "confs": [], "x0": left, "y0": top, "x1": right, "y1": bottom,
            })
            line["words"].append(word)
            line["confs"].append(conf)
            line["x0"] = min(line["x0"], left)
            line["y0"] = min(line["y0"], top)
            line["x1"] = max(line["x1"], right)
            line["y1"] = max(line["y1"], bottom)

        blocks = []
        for line in sorted(lines.values(), key=lambda l: (l["y0"], l["x0"])):
            bbox = [
                max(0, line["x0"]),
                max(0, line["y0"]),
                min(line["x1"], width),
                min(line["y1"], height),
            ]
            if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                continue
            blocks.append({
                "text": " ".join(line["words"]),
                "confidence": round(sum(line["confs"]) / len(line["confs"]) / 100.0, 4),
                "bbox": bbox,
                "block_type": "text",
            })

        full_text = "\n".join(b["text"] for b in blocks)
        overall_confidence = (
            round(sum(b["confidence"] for b in blocks) / len(blocks), 4) if blocks else 0.0
        )

        return {
            "page_number": page_number,
            "text": full_text,
            "confidence": overall_confidence,
            "layout_blocks": blocks,
            "source_reference": source_reference,
        }


def get_ocr_adapter() -> OCRAdapterInterface:
    """Returns the configured OCR adapter (settings.OCR_ENGINE: 'deterministic' or 'tesseract')."""
    from app.core.config import settings

    if settings.OCR_ENGINE == "tesseract":
        return TesseractOCRAdapter()
    return DeterministicOCRAdapter()


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
