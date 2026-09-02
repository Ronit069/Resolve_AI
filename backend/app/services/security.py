import hashlib
import uuid
from typing import Tuple
from fastapi import UploadFile, HTTPException

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit
CHUNK_SIZE = 8192

MAGIC_BYTES = {
    b'%PDF-': ('application/pdf', '.pdf'),
    b'\xFF\xD8\xFF': ('image/jpeg', '.jpeg'),  # JPEG magic starts with FF D8 FF
    b'\x89PNG\r\n\x1A\n': ('image/png', '.png')
}

class SecurityValidationResult:
    def __init__(self, mime_type: str, file_size: int, sha256_hash: str):
        self.mime_type = mime_type
        self.file_size = file_size
        self.sha256_hash = sha256_hash

def validate_and_hash_upload(upload_file: UploadFile) -> SecurityValidationResult:
    """
    Validates the uploaded file safely using bounded chunk reads, magic bytes, 
    and calculates its SHA-256 hash incrementally.
    Rejects files that exceed MAX_FILE_SIZE_BYTES or lack supported magic bytes.
    Does NOT trust the user-supplied filename extension or Content-Type.
    """
    total_bytes = 0
    sha256_hasher = hashlib.sha256()
    detected_mime = None
    
    first_chunk = upload_file.file.read(CHUNK_SIZE)
    if not first_chunk:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # 1. Magic byte validation on first chunk
    for magic, (mime, ext) in MAGIC_BYTES.items():
        if first_chunk.startswith(magic):
            detected_mime = mime
            break
            
    if not detected_mime:
        raise HTTPException(status_code=415, detail="Unsupported file format. Content signature does not match allowed types (PDF, JPEG, PNG).")

    # 2. Process first chunk
    total_bytes += len(first_chunk)
    sha256_hasher.update(first_chunk)
    
    # 3. Read remaining chunks with strict limit
    while True:
        chunk = upload_file.file.read(CHUNK_SIZE)
        if not chunk:
            break
            
        total_bytes += len(chunk)
        if total_bytes > MAX_FILE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes.")
            
        sha256_hasher.update(chunk)
        
    # Reset file pointer for subsequent storage operations
    upload_file.file.seek(0)
    
    return SecurityValidationResult(
        mime_type=detected_mime,
        file_size=total_bytes,
        sha256_hash=sha256_hasher.hexdigest()
    )

def generate_secure_object_key(case_id: str) -> str:
    """Generates a random object key preventing path traversal."""
    return f"evidence/{case_id}/{uuid.uuid4()}"
