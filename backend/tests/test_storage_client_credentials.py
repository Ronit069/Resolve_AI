"""
Issue #14 -- StorageClient must support IAM-role-based credentials for a
real AWS deployment (no long-lived static keys required), while MinIO's
local-dev/CI setup (which always needs explicit static credentials) keeps
working unchanged. Also covers S3_REGION, since forcing 'us-east-1' broke
any real bucket created in another region.
"""
from unittest.mock import patch, MagicMock

from app.core.config import settings


def _make_client_with_patched_settings(**overrides):
    with patch.multiple(settings, **overrides):
        with patch("app.core.storage.boto3.client") as mock_boto_client:
            mock_s3 = MagicMock()
            mock_boto_client.return_value = mock_s3
            mock_s3.head_bucket.return_value = {}

            from app.core.storage import StorageClient
            StorageClient()

            return mock_boto_client


def test_static_credentials_passed_through_when_both_set():
    mock_boto_client = _make_client_with_patched_settings(
        S3_ACCESS_KEY="minio_admin", S3_SECRET_KEY="minio_password", S3_REGION="us-east-1"
    )
    _, kwargs = mock_boto_client.call_args
    assert kwargs["aws_access_key_id"] == "minio_admin"
    assert kwargs["aws_secret_access_key"] == "minio_password"
    assert kwargs["region_name"] == "us-east-1"


def test_credentials_omitted_when_both_unset_falls_back_to_iam_role():
    mock_boto_client = _make_client_with_patched_settings(S3_ACCESS_KEY=None, S3_SECRET_KEY=None)
    _, kwargs = mock_boto_client.call_args
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs


def test_credentials_omitted_when_only_one_is_set():
    # A half-configured pair is never passed as a mismatched partial --
    # boto3 requires both or neither.
    mock_boto_client = _make_client_with_patched_settings(S3_ACCESS_KEY="only-one", S3_SECRET_KEY=None)
    _, kwargs = mock_boto_client.call_args
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs


def test_custom_region_is_passed_through():
    mock_boto_client = _make_client_with_patched_settings(S3_REGION="ap-south-1")
    _, kwargs = mock_boto_client.call_args
    assert kwargs["region_name"] == "ap-south-1"
