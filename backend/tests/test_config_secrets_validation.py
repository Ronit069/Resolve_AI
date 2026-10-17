"""
Tests for Settings._reject_placeholder_secrets_outside_dev -- the
startup check that refuses to boot with placeholder/dummy secrets when
ENVIRONMENT is production-like. See backend/SECRETS.md.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_BASE_ENV = {
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "a-real-password",
    "POSTGRES_DB": "d",
    "POSTGRES_HOST": "h",
    "POSTGRES_PORT": "5432",
    "REDIS_URL": "redis://h:6379/0",
    "S3_ENDPOINT_URL": "https://s3.example.com",
    "S3_ACCESS_KEY": "AKIAREALACCESSKEY",
    "S3_SECRET_KEY": "a-real-s3-secret",
    "S3_BUCKET_NAME": "bucket",
    "API_SECRET_KEY": "a-real-jwt-signing-secret",
    "RAZORPAY_WEBHOOK_SECRET": "a-real-webhook-secret",
    "RAZORPAY_KEY_ID": "rzp_live_realkeyid",
    "RAZORPAY_KEY_SECRET": "a-real-razorpay-secret",
    "OPENAI_API_KEY": "sk-a-real-openai-key",
    "GROQ_API_KEY": "gsk-a-real-groq-key",
}


def test_development_allows_dummy_secrets():
    env = {**_BASE_ENV, "ENVIRONMENT": "development", "OPENAI_API_KEY": "sk-dummy", "GROQ_API_KEY": "gsk_dummy"}
    settings = Settings(_env_file=None, **env)
    assert settings.ENVIRONMENT == "development"


def test_production_accepts_real_looking_secrets():
    env = {**_BASE_ENV, "ENVIRONMENT": "production"}
    settings = Settings(_env_file=None, **env)
    assert settings.ENVIRONMENT == "production"


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "PRODUCTION"])
def test_production_like_rejects_code_default_dummy_keys(environment):
    env = {**_BASE_ENV, "ENVIRONMENT": environment, "OPENAI_API_KEY": "sk-dummy", "GROQ_API_KEY": "gsk_dummy"}
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, **env)


def test_production_rejects_env_example_style_placeholder():
    env = {**_BASE_ENV, "ENVIRONMENT": "production", "API_SECRET_KEY": "your_secret_key_here"}
    with pytest.raises(ValidationError, match="API_SECRET_KEY"):
        Settings(_env_file=None, **env)


def test_production_rejects_this_projects_own_dev_test_values():
    env = {**_BASE_ENV, "ENVIRONMENT": "production", "RAZORPAY_WEBHOOK_SECRET": "testsecret"}
    with pytest.raises(ValidationError, match="RAZORPAY_WEBHOOK_SECRET"):
        Settings(_env_file=None, **env)


def test_error_message_lists_every_offending_field_not_just_the_first():
    env = {**_BASE_ENV, "ENVIRONMENT": "production", "OPENAI_API_KEY": "sk-dummy", "GROQ_API_KEY": "gsk_dummy"}
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **env)
    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "GROQ_API_KEY" in message
