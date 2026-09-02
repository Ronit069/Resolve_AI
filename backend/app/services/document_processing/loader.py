import os
import tempfile
import hashlib
from typing import Generator
from contextlib import contextmanager

from app.core.storage import storage_client
from app.services.intelligence import IntelligenceError

@contextmanager
def download_and_verify_evidence(object_key: str, expected_sha256: str) -> Generator[str, None, None]:
    """
    Retrieves the private object in bounded chunks, calculates SHA-256,
    verifies integrity against the expected hash, and yields the temporary
    secure file path. The file is cleaned up after processing or on error.
    """
    # Create secure temporary file
    fd, temp_path = tempfile.mkstemp(prefix="evid_", dir=tempfile.gettempdir())
    
    try:
        hasher = hashlib.sha256()
        
        # We need to stream the object from MinIO to the temp file
        try:
            # We use the underlying boto3 client to stream
            response = storage_client.s3_client.get_object(
                Bucket=storage_client.bucket,
                Key=object_key
            )
            
            with os.fdopen(fd, 'wb') as f:
                for chunk in response['Body'].iter_chunks(chunk_size=8192):
                    f.write(chunk)
                    hasher.update(chunk)
                    
        except Exception as e:
            # Mask the internal minio error, could be OBJECT_NOT_FOUND or permissions
            os.close(fd) # in case fd was not closed
            raise IntelligenceError("OBJECT_NOT_FOUND", "Failed to retrieve evidence object") from e

        calculated_hash = hasher.hexdigest()
        
        if calculated_hash != expected_sha256:
            raise IntelligenceError("HASH_MISMATCH", "Integrity check failed: Evidence has been tampered with or corrupted")

        yield temp_path

    finally:
        # Cleanup temporary storage
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass # Best effort cleanup
