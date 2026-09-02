import hashlib
import boto3
import uuid
from typing import Optional, Union
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.module_g import KnowledgeSource, GSourceType, GSourceStatus
from app.core.config import settings

class KnowledgeIngestionService:
    def __init__(self, s3_client=None):
        self.s3_client = s3_client or boto3.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY
        )
        self.bucket = settings.S3_BUCKET_NAME

    def _compute_checksum(self, text: str) -> str:
        """
        Computes deterministic SHA-256 on the exact UTF-8 payload.
        """
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def _upload_to_s3(self, content: str, source_type: GSourceType, merchant_id: Optional[uuid.UUID]) -> str:
        """
        Uploads raw text to object storage and returns the deterministic URI.
        """
        tenant_path = str(merchant_id) if merchant_id else 'global'
        object_key = f"knowledge_sources/{tenant_path}/{source_type.value}/{uuid.uuid4()}.txt"
        
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=content.encode('utf-8'),
            ContentType='text/plain'
        )
        return f"s3://{self.bucket}/{object_key}"

    def ingest(
        self,
        db: Session,
        source_type: GSourceType,
        title: str,
        raw_content: Union[str, bytes],
        reason_code: Optional[str] = None,
        merchant_id: Optional[uuid.UUID] = None,
        metadata: Optional[dict] = None
    ) -> KnowledgeSource:
        """
        Ingests a knowledge source. Enforces idempotency and version tracking.
        """
        # Validate merchant_id boundary based on source type (if Razorpay, must be None, etc.)
        if source_type == GSourceType.RAZORPAY_POLICY and merchant_id is not None:
            raise ValueError("RAZORPAY_POLICY is a global policy and must have merchant_id=None.")
        if source_type != GSourceType.RAZORPAY_POLICY and merchant_id is None:
            raise ValueError(f"{source_type.value} is merchant-specific and must have a valid merchant_id.")

        # Decode raw bytes if necessary
        text_content = raw_content.decode('utf-8') if isinstance(raw_content, bytes) else raw_content
        checksum = self._compute_checksum(text_content)

        # Look up existing ACTIVE source for logical identity
        existing_active = db.query(KnowledgeSource).filter(
            KnowledgeSource.merchant_id == merchant_id,
            KnowledgeSource.source_type == source_type,
            KnowledgeSource.reason_code == reason_code,
            KnowledgeSource.status == GSourceStatus.ACTIVE
        ).first()

        if existing_active:
            if existing_active.content_checksum == checksum:
                # Idempotent match: do nothing, return existing
                return existing_active
            
            # Content changed, deprecate the active one
            existing_active.status = GSourceStatus.DEPRECATED
            existing_active.effective_to = datetime.now(timezone.utc)
            db.add(existing_active)

        # Determine next version
        max_ver_row = db.query(KnowledgeSource).filter(
            KnowledgeSource.merchant_id == merchant_id,
            KnowledgeSource.source_type == source_type,
            KnowledgeSource.reason_code == reason_code
        ).order_by(KnowledgeSource.version.desc()).first()
        
        new_version = (max_ver_row.version + 1) if max_ver_row else 1

        # Upload to MinIO/S3 only when inserting a new record
        source_uri = self._upload_to_s3(text_content, source_type, merchant_id)

        meta = metadata.copy() if metadata else {}
        meta["source_uri"] = source_uri

        new_source = KnowledgeSource(
            merchant_id=merchant_id,
            source_type=source_type,
            reason_code=reason_code,
            title=title,
            content_checksum=checksum,
            version=new_version,
            status=GSourceStatus.ACTIVE,
            metadata_json=meta,
            effective_from=datetime.now(timezone.utc)
        )
        db.add(new_source)

        try:
            db.commit()
            db.refresh(new_source)
            return new_source
        except IntegrityError:
            db.rollback()
            raise
