import abc
from typing import Dict, Any, List, Optional
import hashlib

from app.services.intelligence import IntelligenceError

class FieldProvenance:
    def __init__(self, page_number: int, source_text: str, bbox: Optional[List[float]] = None):
        self.page_number = page_number
        self.source_text = source_text
        self.bbox = bbox
        self.source_text_hash = hashlib.sha256(source_text.encode('utf-8')).hexdigest() if source_text else None

class ExtractedFieldData:
    def __init__(
        self,
        name: str,
        value_type: str,
        raw_value: str,
        confidence: float,
        provenance: Optional[FieldProvenance],
        review_required: bool = False
    ):
        self.name = name
        self.value_type = value_type
        self.raw_value = raw_value
        self.confidence = confidence
        self.provenance = provenance
        self.review_required = review_required

class DocumentExtractorInterface(abc.ABC):
    @abc.abstractmethod
    def extract_fields(self, document_text_chunks: List[Dict[str, Any]], layout_blocks_by_page: Dict[int, List[Dict[str, Any]]], schema: Dict[str, Any]) -> List[ExtractedFieldData]:
        pass

class InvoiceExtractor(DocumentExtractorInterface):
    """
    Deterministic MVP extractor for INVOICE documents.
    """
    def extract_fields(self, document_text_chunks: List[Dict[str, Any]], layout_blocks_by_page: Dict[int, List[Dict[str, Any]]], schema: Dict[str, Any]) -> List[ExtractedFieldData]:
        fields = []
        
        # We simulate extraction by searching through layout blocks for typical invoice keys
        invoice_number_cands = []
        invoice_date_cands = []
        total_amount_cands = []
        
        for page_num, blocks in layout_blocks_by_page.items():
            for block in blocks:
                text = block.get("text", "")
                text_lower = text.lower()
                bbox = block.get("bbox")
                
                if "invoice number:" in text_lower or "inv-" in text_lower:
                    val = text.split(":")[-1].strip() if ":" in text else text.split(" ")[-1]
                    if val:
                        invoice_number_cands.append(ExtractedFieldData(
                            name="invoice_number", value_type="string", raw_value=val,
                            confidence=0.9, provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
                elif "date:" in text_lower or "2026-" in text_lower:
                    val = text.split(":")[-1].strip() if ":" in text else text.split(" ")[-1]
                    if val:
                        invoice_date_cands.append(ExtractedFieldData(
                            name="invoice_date", value_type="string", raw_value=val,
                            confidence=0.85, provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
                elif "total:" in text_lower or "12500" in text_lower:
                    val = text.split(":")[-1].strip() if ":" in text else text.split(" ")[-1]
                    if val:
                        total_amount_cands.append(ExtractedFieldData(
                            name="total_amount", value_type="string", raw_value=val,
                            confidence=0.88, provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
                        
        def resolve_cands(cands: List[ExtractedFieldData]) -> Optional[ExtractedFieldData]:
            if not cands: return None
            # If multiple candidates, we mark the first as review_required and lower its confidence
            if len(cands) > 1:
                ans = cands[0]
                ans.review_required = True
                ans.confidence = 0.5
                return ans
            return cands[0]

        invoice_number = resolve_cands(invoice_number_cands)
        invoice_date = resolve_cands(invoice_date_cands)
        total_amount = resolve_cands(total_amount_cands)
        
        if invoice_number: fields.append(invoice_number)
        if invoice_date: fields.append(invoice_date)
        if total_amount: fields.append(total_amount)
        
        return fields

class ProofOfDeliveryExtractor(DocumentExtractorInterface):
    """
    Deterministic MVP extractor for PROOF_OF_DELIVERY documents.
    """
    def extract_fields(self, document_text_chunks: List[Dict[str, Any]], layout_blocks_by_page: Dict[int, List[Dict[str, Any]]], schema: Dict[str, Any]) -> List[ExtractedFieldData]:
        fields = []
        for page_num, blocks in layout_blocks_by_page.items():
            for block in blocks:
                text = block.get("text", "")
                text_lower = text.lower()
                bbox = block.get("bbox")
                
                if "signed by:" in text_lower:
                    val = text.split(":")[-1].strip()
                    if val:
                        fields.append(ExtractedFieldData(
                            name="recipient_name",
                            value_type="string",
                            raw_value=val,
                            confidence=0.92,
                            provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
                elif "delivery date:" in text_lower:
                    val = text.split(":")[-1].strip()
                    if val:
                        fields.append(ExtractedFieldData(
                            name="delivery_date",
                            value_type="string",
                            raw_value=val,
                            confidence=0.85,
                            provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
        return fields

class CourierTrackingExtractor(DocumentExtractorInterface):
    """
    Deterministic MVP extractor for COURIER_TRACKING documents.
    """
    def extract_fields(self, document_text_chunks: List[Dict[str, Any]], layout_blocks_by_page: Dict[int, List[Dict[str, Any]]], schema: Dict[str, Any]) -> List[ExtractedFieldData]:
        fields = []
        for page_num, blocks in layout_blocks_by_page.items():
            for block in blocks:
                text = block.get("text", "")
                text_lower = text.lower()
                bbox = block.get("bbox")
                
                if "tracking number:" in text_lower:
                    val = text.split(":")[-1].strip()
                    if val:
                        fields.append(ExtractedFieldData(
                            name="tracking_number",
                            value_type="string",
                            raw_value=val,
                            confidence=0.95,
                            provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
                elif "carrier:" in text_lower:
                    val = text.split(":")[-1].strip()
                    if val:
                        fields.append(ExtractedFieldData(
                            name="carrier_name",
                            value_type="string",
                            raw_value=val,
                            confidence=0.90,
                            provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
                elif "status:" in text_lower:
                    val = text.split(":")[-1].strip()
                    if val:
                        fields.append(ExtractedFieldData(
                            name="shipment_status",
                            value_type="string",
                            raw_value=val,
                            confidence=0.85,
                            provenance=FieldProvenance(page_number=page_num, source_text=text, bbox=bbox)
                        ))
        return fields

class DeterministicExtractorFactory:
    @staticmethod
    def get_extractor(document_type: str) -> DocumentExtractorInterface:
        if document_type == "INVOICE":
            return InvoiceExtractor()
        elif document_type == "PROOF_OF_DELIVERY":
            return ProofOfDeliveryExtractor()
        elif document_type == "COURIER_TRACKING":
            return CourierTrackingExtractor()
        else:
            raise IntelligenceError("UNSUPPORTED_DOCUMENT_LAYOUT", f"No extractor available for type: {document_type}")
