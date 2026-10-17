"""
Issue #16 -- optional Sentry error tracking. Unset SENTRY_DSN must be a
complete no-op; init_sentry() must never raise regardless of what fails.
"""
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.core.error_tracking import init_sentry


def test_noop_when_dsn_unset():
    with patch.object(settings, "SENTRY_DSN", None):
        with patch("sentry_sdk.init") as mock_init:
            init_sentry()
            mock_init.assert_not_called()


def test_calls_sentry_init_with_dsn_and_environment_when_set():
    with patch.object(settings, "SENTRY_DSN", "https://example@sentry.io/1"), \
         patch.object(settings, "ENVIRONMENT", "production"), \
         patch.object(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.1), \
         patch("sentry_sdk.init") as mock_init:
        init_sentry()
        mock_init.assert_called_once()
        _, kwargs = mock_init.call_args
        assert kwargs["dsn"] == "https://example@sentry.io/1"
        assert kwargs["environment"] == "production"
        assert kwargs["traces_sample_rate"] == 0.1


def test_uses_celery_integration_when_celery_flag_set():
    with patch.object(settings, "SENTRY_DSN", "https://example@sentry.io/1"), \
         patch("sentry_sdk.init") as mock_init:
        init_sentry(celery=True)
        _, kwargs = mock_init.call_args
        integration_names = [type(i).__name__ for i in kwargs["integrations"]]
        assert "CeleryIntegration" in integration_names


def test_uses_fastapi_integration_by_default():
    with patch.object(settings, "SENTRY_DSN", "https://example@sentry.io/1"), \
         patch("sentry_sdk.init") as mock_init:
        init_sentry(celery=False)
        _, kwargs = mock_init.call_args
        integration_names = [type(i).__name__ for i in kwargs["integrations"]]
        assert "FastApiIntegration" in integration_names


def test_never_raises_when_sentry_init_itself_fails():
    with patch.object(settings, "SENTRY_DSN", "https://example@sentry.io/1"), \
         patch("sentry_sdk.init", side_effect=RuntimeError("network unreachable")):
        init_sentry()  # must not raise
