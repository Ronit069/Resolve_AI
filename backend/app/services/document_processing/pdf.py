import fitz  # PyMuPDF
import io
import uuid
from typing import List, Dict, Any, Tuple
from sqlalchemy.orm import Session
import datetime

from app.core.config import settings
from app.services.intelligence import IntelligenceError
from app.models.module_d import DocumentPage
from app.core.storage import storage_client
from app.services.document_processing.preprocessor import StandardPreprocessor

class PDFProcessor:
    @staticmethod
    def process_pdf(
        db: Session,
        file_path: str,
        job_id: uuid.UUID,
        document_id: uuid.UUID,
        case_id: uuid.UUID
    ) -> List[DocumentPage]:
        """
        Processes a PDF document:
        - Validates limits and corruption
        - Attempts native text extraction
        - Renders pages if native text is insufficient
        - Saves derived artifacts privately
        - Persists DocumentPage records
        """
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            raise IntelligenceError("DOCUMENT_CORRUPTED", "Failed to open PDF document") from e

        if doc.needs_pass:
            raise IntelligenceError("PDF_PASSWORD_PROTECTED", "PDF is password protected")

        page_count = doc.page_count
        if page_count > settings.MAX_PDF_PAGES:
            raise IntelligenceError(
                "PAGE_LIMIT_EXCEEDED", 
                f"PDF has {page_count} pages, which exceeds the limit of {settings.MAX_PDF_PAGES}"
            )

        pages_metadata = []

        for page_num in range(page_count):
            page = doc.load_page(page_num)
            
            # Deterministic ordering is 1-based
            actual_page_number = page_num + 1
            
            # Native text extraction
            text = page.get_text("text")
            native_text_used = len(text.strip()) >= settings.MIN_NATIVE_TEXT_LENGTH

            artifact_key = None
            ocr_text_key = None
            preprocessing_metadata = None

            # If native text is insufficient, render page for OCR
            if not native_text_used:
                pix = page.get_pixmap(dpi=150)
                raw_img_data = pix.tobytes("png")
                img_data, preprocessing_metadata = StandardPreprocessor().preprocess_image(raw_img_data)
                artifact_key = f"derived/{case_id}/{document_id}/{job_id}/page_{actual_page_number}.png"
                storage_client.upload_file(io.BytesIO(img_data), artifact_key, content_type="image/png")
            else:
                # Save native text privately
                import json
                ocr_text_key = f"derived/{case_id}/{document_id}/{job_id}/page_{actual_page_number}_text.json"
                text_payload = json.dumps({"text": text, "source": "native", "page_number": actual_page_number}).encode("utf-8")
                storage_client.upload_file(io.BytesIO(text_payload), ocr_text_key, content_type="application/json")

            rect = page.rect
            rotation = page.rotation

            if preprocessing_metadata is not None:
                # width_px/height_px describe the rendered+preprocessed
                # artifact actually stored at artifact_key (what OCR bbox
                # validation bounds against), not the PDF's native
                # point-space page rect, which get_pixmap(dpi=150) and
                # StandardPreprocessor's own resizing both change.
                page_width_px = preprocessing_metadata["output_width_px"]
                page_height_px = preprocessing_metadata["output_height_px"]
            else:
                page_width_px = int(rect.width)
                page_height_px = int(rect.height)

            doc_page = DocumentPage(
                job_id=job_id,
                document_id=document_id,
                page_number=actual_page_number,
                width_px=page_width_px,
                height_px=page_height_px,
                rotation_degrees=rotation,
                native_text_used=native_text_used,
                page_artifact_key=artifact_key,
                ocr_text_object_key=ocr_text_key,
                preprocessing_json=preprocessing_metadata
            )
            
            pages_metadata.append(doc_page)

        doc.close()
        return pages_metadata
