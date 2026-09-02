import uuid
from PIL import Image
import io
from sqlalchemy.orm import Session
import os

from app.core.config import settings
from app.services.intelligence import IntelligenceError
from app.models.module_d import DocumentPage
from app.core.storage import storage_client

class ImageProcessor:
    @staticmethod
    def process_image(
        db: Session,
        file_path: str,
        job_id: uuid.UUID,
        document_id: uuid.UUID,
        case_id: uuid.UUID,
        mime_type: str
    ) -> list[DocumentPage]:
        """
        Processes a JPEG/PNG document:
        - Validates dimensions and limits
        - Creates a single logical page
        - Saves a derived processing artifact
        """
        try:
            with Image.open(file_path) as img:
                img.verify() # Verify integrity
        except Exception as e:
            raise IntelligenceError("DOCUMENT_CORRUPTED", "Failed to open or verify image document") from e

        # Reopen after verify
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                
                if width > settings.MAX_IMAGE_WIDTH or height > settings.MAX_IMAGE_HEIGHT:
                    raise IntelligenceError(
                        "UNSUPPORTED_DOCUMENT_LAYOUT",
                        f"Image dimensions ({width}x{height}) exceed limits ({settings.MAX_IMAGE_WIDTH}x{settings.MAX_IMAGE_HEIGHT})"
                    )

                # Convert to standard RGB format if needed to ensure consistency
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                img_data = img_byte_arr.getvalue()

                # Save to private storage as a derived artifact
                artifact_key = f"derived/{case_id}/{document_id}/{job_id}/page_1.png"
                storage_client.upload_file(io.BytesIO(img_data), artifact_key, content_type="image/png")

                # Image is always considered a single page with no native text
                doc_page = DocumentPage(
                    job_id=job_id,
                    document_id=document_id,
                    page_number=1,
                    width_px=width,
                    height_px=height,
                    rotation_degrees=0,
                    native_text_used=False,
                    page_artifact_key=artifact_key,
                    preprocessing_json=None
                )
                
                return [doc_page]
        except IntelligenceError:
            raise
        except Exception as e:
            raise IntelligenceError("DOCUMENT_CORRUPTED", f"Error processing image: {str(e)}") from e
