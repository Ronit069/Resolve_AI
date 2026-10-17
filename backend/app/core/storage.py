import logging

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageUnavailableError(Exception):
    """Object storage could not be reached or an operation failed at the transport level."""


class StorageClient:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name='us-east-1' # Default for MinIO compatibility
        )
        self.bucket = settings.S3_BUCKET_NAME
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self.s3_client.create_bucket(Bucket=self.bucket)
            except (ClientError, BotoCoreError) as e:
                logger.error(f"Error creating bucket {self.bucket}: {e}")
        except BotoCoreError as e:
            # Endpoint unreachable, connection timed out, no credentials, etc.
            # Never let this crash the process at import time -- every call
            # below will fail the same way (and report it) until the
            # endpoint recovers.
            logger.error(f"Object storage unreachable while checking bucket {self.bucket}: {e}")

    def upload_file(self, file_obj, object_name: str, content_type: str = 'application/octet-stream') -> bool:
        """Upload a file-like object to object storage"""
        try:
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket,
                object_name,
                ExtraArgs={'ContentType': content_type}
            )
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to upload file {object_name}: {e}")
            return False

    def generate_presigned_url(self, object_name: str, expiration=3600) -> str:
        """Generate a presigned URL to share an S3 object"""
        try:
            response = self.s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': self.bucket,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
            return response
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to generate presigned url for {object_name}: {e}")
            return None

    def delete_file(self, object_name: str) -> bool:
        """Delete an object from storage (useful for compensation/rollback)"""
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=object_name)
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to delete file {object_name}: {e}")
            return False

    def download_file(self, object_name: str) -> bytes:
        """Read an object's full contents from storage (Module J, Category D: Razorpay document upload)."""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=object_name)
            return response["Body"].read()
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to download file {object_name}: {e}")
            raise StorageUnavailableError(f"Failed to download {object_name}: {e}") from e

storage_client = StorageClient()
