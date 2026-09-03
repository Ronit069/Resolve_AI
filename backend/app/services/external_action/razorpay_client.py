"""
Module J, Category D — Razorpay HTTP client.

The only module in this codebase that performs real outbound HTTP calls
to Razorpay. Every other Category D/B module is a pure local computation
or DB operation composed around this boundary.

Frozen Product Owner decisions this client implements:
  - Global credentials (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET) via HTTP
    Basic Auth, not per-merchant.
  - Fail-closed live-mode guard: a key that does not look like a
    test-mode key is refused unless RAZORPAY_ALLOW_LIVE_MODE is
    explicitly True. Checked before any HTTP client object is even
    constructed, so no credential ever reaches the network in that state.
  - Retry-safety classification (see app/worker/external_action_tasks.py
    for how these are used): 429 is the only retryable condition. 4xx
    validation and 401/403 auth failures are terminal. 5xx, a timeout or
    connection error, and an unparseable/malformed 2xx body all collapse
    into RazorpayUnknownResult — terminal and never auto-retried, because
    Razorpay documents no idempotency mechanism for the document-upload
    or dispute-contest endpoints, so whether a request was actually
    applied server-side cannot be safely assumed.
"""

import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayClientError(Exception):
    """Base class for every failure this client can raise."""


class RazorpayLiveModeNotAllowed(RazorpayClientError):
    """
    RAZORPAY_KEY_ID does not start with 'rzp_test_' and
    RAZORPAY_ALLOW_LIVE_MODE is not explicitly True. Raised in
    RazorpayClient.__init__ before the underlying httpx client is
    constructed.
    """


class RazorpayValidationError(RazorpayClientError):
    """A 4xx response other than 401/403/429: terminal, never retried."""
    def __init__(self, status_code: int, error_code: Optional[str], description: Optional[str], raw_body):
        self.status_code = status_code
        self.error_code = error_code
        self.description = description
        self.raw_body = raw_body
        super().__init__(f"Razorpay validation error {status_code}: {error_code} {description}")


class RazorpayAuthError(RazorpayClientError):
    """401 or 403: terminal — a credential/config problem, never retried."""
    def __init__(self, status_code: int, raw_body):
        self.status_code = status_code
        self.raw_body = raw_body
        super().__init__(f"Razorpay auth error {status_code}")


class RazorpayRateLimited(RazorpayClientError):
    """429: the only documented-retryable condition."""
    def __init__(self, raw_body):
        self.raw_body = raw_body
        super().__init__("Razorpay rate limited (429)")


class RazorpayUnknownResult(RazorpayClientError):
    """
    A 5xx response, no response at all (timeout/connection error), or a
    2xx response whose body could not be parsed/did not carry the
    expected shape. Terminal and never auto-retried — see module
    docstring.
    """
    def __init__(self, detail: str, status_code: Optional[int] = None, raw_body=None):
        self.status_code = status_code
        self.raw_body = raw_body
        super().__init__(detail)


def _safe_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return response.text


def _parse_error_envelope(raw_body):
    parsed = raw_body if isinstance(raw_body, dict) else None
    error = (parsed or {}).get("error") or {}
    return error.get("code"), error.get("description")


class RazorpayClient:
    def __init__(self):
        key_id = settings.RAZORPAY_KEY_ID
        if not key_id.startswith("rzp_test_") and not settings.RAZORPAY_ALLOW_LIVE_MODE:
            raise RazorpayLiveModeNotAllowed(
                "RAZORPAY_KEY_ID does not look like a test-mode key (rzp_test_*) and "
                "RAZORPAY_ALLOW_LIVE_MODE is not True; refusing to construct a live client."
            )
        self._client = httpx.Client(
            base_url=RAZORPAY_BASE_URL,
            auth=(key_id, settings.RAZORPAY_KEY_SECRET),
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _handle_response(self, response: httpx.Response) -> dict:
        status = response.status_code

        if status == 429:
            raise RazorpayRateLimited(raw_body=_safe_json(response))

        if status in (401, 403):
            raise RazorpayAuthError(status_code=status, raw_body=_safe_json(response))

        if 400 <= status < 500:
            body = _safe_json(response)
            error_code, description = _parse_error_envelope(body)
            raise RazorpayValidationError(status_code=status, error_code=error_code, description=description, raw_body=body)

        if status >= 500:
            raise RazorpayUnknownResult(
                f"Razorpay returned a server error ({status})", status_code=status, raw_body=_safe_json(response),
            )

        try:
            return response.json()
        except ValueError:
            raise RazorpayUnknownResult(
                "Razorpay returned a 2xx response with an unparseable body", status_code=status, raw_body=response.text,
            )

    def upload_document(self, file_bytes: bytes, filename: str, content_type: str, purpose: str = "dispute_evidence") -> dict:
        try:
            response = self._client.post(
                "/documents",
                data={"purpose": purpose},
                files={"file": (filename, file_bytes, content_type)},
            )
        except httpx.TimeoutException as exc:
            raise RazorpayUnknownResult(f"Timed out calling POST /documents: {exc}") from exc
        except httpx.TransportError as exc:
            raise RazorpayUnknownResult(f"Transport error calling POST /documents: {exc}") from exc

        return self._handle_response(response)

    def contest_dispute(self, razorpay_dispute_id: str, body: dict) -> dict:
        try:
            response = self._client.patch(f"/disputes/{razorpay_dispute_id}/contest", json=body)
        except httpx.TimeoutException as exc:
            raise RazorpayUnknownResult(f"Timed out calling PATCH /disputes/{razorpay_dispute_id}/contest: {exc}") from exc
        except httpx.TransportError as exc:
            raise RazorpayUnknownResult(f"Transport error calling PATCH /disputes/{razorpay_dispute_id}/contest: {exc}") from exc

        return self._handle_response(response)
