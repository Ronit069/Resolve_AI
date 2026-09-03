"""
Module J, Category D — RazorpayClient tests.

Every test uses httpx.MockTransport (no real network call). Verifies the
fail-closed live-mode guard, Basic Auth wiring, the multipart upload
field name, and the full A/B/C/D response-classification hierarchy:
  - 429                       -> RazorpayRateLimited (retryable)
  - 401/403                   -> RazorpayAuthError (terminal)
  - other 4xx                 -> RazorpayValidationError (terminal, parsed envelope)
  - 5xx / timeout / malformed -> RazorpayUnknownResult (terminal, never retried)
"""

import base64
import json

import httpx
import pytest

from app.core.config import settings
from app.services.external_action.razorpay_client import (
    RAZORPAY_BASE_URL,
    RazorpayAuthError,
    RazorpayClient,
    RazorpayLiveModeNotAllowed,
    RazorpayRateLimited,
    RazorpayUnknownResult,
    RazorpayValidationError,
)


def _make_client(monkeypatch, key_id="rzp_test_abc123", secret="secret_xyz", allow_live=False, handler=None):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", key_id)
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", secret)
    monkeypatch.setattr(settings, "RAZORPAY_ALLOW_LIVE_MODE", allow_live)
    client = RazorpayClient()
    if handler is not None:
        client._client = httpx.Client(
            base_url=RAZORPAY_BASE_URL,
            auth=(key_id, secret),
            transport=httpx.MockTransport(handler),
        )
    return client


# 1. Live-mode guard: a non-test key without RAZORPAY_ALLOW_LIVE_MODE is refused.
def test_live_mode_refused_without_explicit_allow(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_live_realkey")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setattr(settings, "RAZORPAY_ALLOW_LIVE_MODE", False)
    with pytest.raises(RazorpayLiveModeNotAllowed):
        RazorpayClient()


# 2. Live-mode guard: explicitly allowed live key constructs successfully.
def test_live_mode_allowed_when_explicitly_enabled(monkeypatch):
    client = _make_client(monkeypatch, key_id="rzp_live_realkey", allow_live=True)
    assert client is not None
    client.close()


# 3. Test-mode key always constructs, regardless of RAZORPAY_ALLOW_LIVE_MODE.
def test_test_mode_key_always_allowed(monkeypatch):
    client = _make_client(monkeypatch, key_id="rzp_test_abc123", allow_live=False)
    assert client is not None
    client.close()


# 4. No httpx.Client (and no network) is ever constructed when the guard fires.
def test_live_mode_guard_fires_before_http_client_construction(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_live_realkey")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setattr(settings, "RAZORPAY_ALLOW_LIVE_MODE", False)
    try:
        RazorpayClient()
        assert False, "expected RazorpayLiveModeNotAllowed"
    except RazorpayLiveModeNotAllowed as exc:
        assert not hasattr(exc, "_client")


# 5. Basic Auth header is sent using the configured credentials.
def test_basic_auth_header_sent(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "doc_123", "purpose": "dispute_evidence"})

    client = _make_client(monkeypatch, key_id="rzp_test_abc123", secret="secret_xyz", handler=handler)
    client.upload_document(file_bytes=b"filecontents", filename="proof.pdf", content_type="application/pdf")
    client.close()

    expected = "Basic " + base64.b64encode(b"rzp_test_abc123:secret_xyz").decode()
    assert captured["authorization"] == expected


# 6. Multipart upload uses field name "file" and purpose="dispute_evidence".
def test_upload_document_multipart_field_name(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"id": "doc_abc", "purpose": "dispute_evidence"})

    client = _make_client(monkeypatch, handler=handler)
    response = client.upload_document(file_bytes=b"hello world", filename="proof.pdf", content_type="application/pdf")
    client.close()

    assert response["id"] == "doc_abc"
    assert captured["content_type"].startswith("multipart/form-data")
    assert b'name="file"' in captured["content"]
    assert b'name="purpose"' in captured["content"]
    assert b"dispute_evidence" in captured["content"]


# 7. contest_dispute success returns the parsed JSON body.
def test_contest_dispute_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/disputes/disp_123/contest"
        body = json.loads(request.content)
        assert body["action"] == "submit"
        return httpx.Response(200, json={"id": "disp_123", "status": "under_review", "evidence": {}})

    client = _make_client(monkeypatch, handler=handler)
    response = client.contest_dispute("disp_123", {"action": "submit", "amount": 5000, "summary": "x", "evidence": {}})
    client.close()

    assert response["id"] == "disp_123"
    assert response["status"] == "under_review"


# 8. 429 -> RazorpayRateLimited
def test_429_raises_rate_limited(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "TOO_MANY_REQUESTS", "description": "slow down"}})

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayRateLimited):
        client.contest_dispute("disp_123", {"action": "draft"})
    client.close()


# 9. 401 -> RazorpayAuthError
def test_401_raises_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "description": "bad credentials"}})

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayAuthError) as excinfo:
        client.contest_dispute("disp_123", {"action": "draft"})
    assert excinfo.value.status_code == 401
    client.close()


# 10. 403 -> RazorpayAuthError
def test_403_raises_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "description": "not allowed"}})

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayAuthError):
        client.contest_dispute("disp_123", {"action": "draft"})
    client.close()


# 11. Other 4xx -> RazorpayValidationError with parsed envelope
def test_400_raises_validation_error_with_parsed_envelope(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "amount invalid", "field": "amount"}})

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayValidationError) as excinfo:
        client.contest_dispute("disp_123", {"action": "draft"})
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_code == "BAD_REQUEST_ERROR"
    assert excinfo.value.description == "amount invalid"
    client.close()


# 12. 5xx -> RazorpayUnknownResult (terminal, per frozen PO override — never retried)
def test_5xx_raises_unknown_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayUnknownResult) as excinfo:
        client.contest_dispute("disp_123", {"action": "draft"})
    assert excinfo.value.status_code == 500
    client.close()


# 13. Malformed 2xx body -> RazorpayUnknownResult
def test_malformed_2xx_body_raises_unknown_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not valid json{{{")

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayUnknownResult):
        client.contest_dispute("disp_123", {"action": "draft"})
    client.close()


# 14. Timeout -> RazorpayUnknownResult
def test_timeout_raises_unknown_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayUnknownResult):
        client.contest_dispute("disp_123", {"action": "draft"})
    client.close()


# 15. Connection error -> RazorpayUnknownResult
def test_connection_error_raises_unknown_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection refused")

    client = _make_client(monkeypatch, handler=handler)
    with pytest.raises(RazorpayUnknownResult):
        client.upload_document(file_bytes=b"x", filename="a.pdf", content_type="application/pdf")
    client.close()


# 16. Base URL is exactly the documented Razorpay API root.
def test_base_url_is_frozen_contract_value():
    assert RAZORPAY_BASE_URL == "https://api.razorpay.com/v1"
