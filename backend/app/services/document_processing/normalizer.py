from typing import Optional
from decimal import Decimal, InvalidOperation
from datetime import datetime
import re

from app.models.module_d import ExtractedField
from app.services.intelligence import IntelligenceError

class NormalizerInterface:
    def normalize_field(self, field: ExtractedField) -> None:
        """
        Normalizes the given field by setting its canonical values based on its raw_value_masked.
        Updates the field object in-place.
        """
        pass

class IdentifierNormalizer(NormalizerInterface):
    def normalize_field(self, field: ExtractedField) -> None:
        if not field.raw_value_masked:
            return
            
        # Strip whitespace, keep semantic characters
        normalized = field.raw_value_masked.strip()
        field.canonical_value_text = normalized
        field.value_type = "identifier"

class DateNormalizer(NormalizerInterface):
    def normalize_field(self, field: ExtractedField) -> None:
        if not field.raw_value_masked:
            return
            
        raw = field.raw_value_masked.strip()
        
        # Determine if it's ambiguous (e.g. 03/04/2026 without text month context)
        # We will parse specific supported formats.
        
        # 2026-08-20 (ISO)
        iso_match = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', raw)
        if iso_match:
            try:
                dt = datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
                field.canonical_value_text = dt.strftime("%Y-%m-%d")
                field.date_value = dt
                field.value_type = "date"
                return
            except ValueError:
                field.review_status = "REVIEW_REQUIRED"
                return
                
        # 20-Aug-2026 (Unambiguous)
        unambiguous_match = re.match(r'^(\d{1,2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{4})$', raw, re.IGNORECASE)
        if unambiguous_match:
            try:
                dt = datetime.strptime(raw, "%d-%b-%Y")
                field.canonical_value_text = dt.strftime("%Y-%m-%d")
                field.date_value = dt
                field.value_type = "date"
                return
            except ValueError:
                field.review_status = "REVIEW_REQUIRED"
                return

        # Ambiguous 03/04/2026
        ambig_match = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', raw)
        if ambig_match:
            d1 = int(ambig_match.group(1))
            d2 = int(ambig_match.group(2))
            
            # If d1 > 12, it must be DD/MM/YYYY. If d2 > 12, it must be MM/DD/YYYY.
            # If both <= 12, it's strictly ambiguous contextually.
            if d1 > 12 and d2 <= 12:
                # DD/MM/YYYY
                dt = datetime(int(ambig_match.group(3)), d2, d1)
                field.canonical_value_text = dt.strftime("%Y-%m-%d")
                field.date_value = dt
                field.value_type = "date"
            elif d2 > 12 and d1 <= 12:
                # MM/DD/YYYY
                dt = datetime(int(ambig_match.group(3)), d1, d2)
                field.canonical_value_text = dt.strftime("%Y-%m-%d")
                field.date_value = dt
                field.value_type = "date"
            else:
                # Ambiguous
                field.review_status = "REVIEW_REQUIRED"
                
            return

        # Unknown format
        field.review_status = "REVIEW_REQUIRED"

class MoneyNormalizer(NormalizerInterface):
    def normalize_field(self, field: ExtractedField) -> None:
        if not field.raw_value_masked:
            return
            
        raw = field.raw_value_masked.strip()
        
        # Remove commas
        clean_val = raw.replace(',', '')
        
        # Extract currency hints
        currency = None
        if '₹' in clean_val or 'INR' in clean_val.upper() or 'RS.' in clean_val.upper():
            currency = 'INR'
            clean_val = re.sub(r'[₹]|[Ii][Nn][Rr]|[Rr][Ss]\.?', '', clean_val).strip()
        
        if not currency:
            # Missing currency, require review
            field.review_status = "REVIEW_REQUIRED"
            return
            
        try:
            val = Decimal(clean_val)
            field.numeric_value = val
            field.currency_code = currency
            field.canonical_value_text = str(val)
            field.value_type = "monetary_amount"
        except InvalidOperation:
            field.review_status = "REVIEW_REQUIRED"

class TextNormalizer(NormalizerInterface):
    def normalize_field(self, field: ExtractedField) -> None:
        if not field.raw_value_masked:
            return
            
        # Safe whitespace normalization
        normalized = re.sub(r'\s+', ' ', field.raw_value_masked).strip()
        field.canonical_value_text = normalized
        field.value_type = "text"


class DeterministicNormalizerRegistry:
    
    @staticmethod
    def get_normalizer(field_name: str) -> NormalizerInterface:
        if field_name in ["invoice_number", "tracking_number"]:
            return IdentifierNormalizer()
        elif field_name in ["invoice_date", "delivery_date"]:
            return DateNormalizer()
        elif field_name in ["total_amount"]:
            return MoneyNormalizer()
        elif field_name in ["recipient_name", "carrier_name", "shipment_status"]:
            return TextNormalizer()
        else:
            return TextNormalizer()
