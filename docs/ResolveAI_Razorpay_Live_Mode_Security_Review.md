# Razorpay Live-Mode Security Review

Scope requested in issue #7: webhook signature verification, the live/test
mode boundary, and outbound credential handling, before enabling live mode
for any merchant. This is a read-through review of the code as it stands
after the #3/#5/#6 changes in this PR — no code changes were made as a
result of it; every check below passed.

## 1. Webhook signature verification

Both inbound webhook endpoints verify `X-Razorpay-Signature` the same way:

- `app/api/endpoints/webhooks.py::razorpay_webhook` (Module A, dispute ingestion)
- `app/api/endpoints/outcome_webhooks.py::razorpay_outcome_webhook` (Module K, outcome feedback)

Each computes `hmac.new(RAZORPAY_WEBHOOK_SECRET, raw_body, hashlib.sha256).hexdigest()`
against the **raw, unparsed request body**, compares it to the header with
`hmac.compare_digest` (constant-time, not `==`), and rejects with 401 before
any JSON parsing happens if the signature is missing or doesn't match. JSON
parsing and schema validation only happen after a verified signature, so an
unauthenticated payload is never acted on in any form.

**Verdict: correct.** No gap found.

## 2. Live/test mode boundary

`app/services/external_action/razorpay_client.py::RazorpayClient.__init__`:

```python
key_id = settings.RAZORPAY_KEY_ID
if not key_id.startswith("rzp_test_") and not settings.RAZORPAY_ALLOW_LIVE_MODE:
    raise RazorpayLiveModeNotAllowed(...)
```

This check runs **before** the underlying `httpx.Client` is constructed, so
a credential that looks live never reaches the network unless
`RAZORPAY_ALLOW_LIVE_MODE` is explicitly `True`. `RazorpayClient` has
exactly one instantiation site in the whole codebase
(`app/worker/external_action_tasks.py::make_razorpay_production_transport`),
so there is no second, unguarded path to a real API call.

One finding worth recording rather than "fixing": **`dispatch_external_action_task`
(the Celery task that wires in the real transport) has zero `.delay()` call
sites anywhere in the application** — it is not triggered by any review
action, webhook, or scheduled job today. This is a product-completeness gap
(the real external-action dispatch is effectively dormant), not a security
defect — if anything it *reduces* the live-mode blast radius right now,
since nothing in the running application can reach Razorpay's live API
without a human manually invoking the task. Flagging it here so it isn't
mistaken for "already wired up and safe" when someone does connect it later
— that connection point deserves its own review for authorization (who can
trigger a real contest submission) when it's built.

**Verdict: correct and fail-closed.** The dormant-dispatch observation is
informational, not a defect.

## 3. Credential handling

- `RAZORPAY_KEY_SECRET` is read in exactly one place (`razorpay_client.py`),
  passed directly into `httpx.Client(auth=(key_id, key_secret))` — httpx's
  native Basic Auth handling, never manually formatted into a header string
  or logged.
- Audit persistence (`app/worker/external_action_tasks.py`) stores Razorpay
  **response** bodies for debugging (`response_metadata`/`raw_body`), never
  the outbound request or its Authorization header; `request_metadata` is
  explicitly scoped to `{action_type, aggregate_id}` only, with an inline
  comment confirming "no credentials, no auth headers, no raw Razorpay
  request body."
- No `verify=False` or other TLS-downgrade anywhere in the codebase — httpx
  verifies certificates by default and nothing overrides that.
- `backend/SECRETS.md` (added in this PR) documents `RAZORPAY_KEY_ID`/
  `RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET`, and the new
  `ENVIRONMENT=production` startup check (same PR) refuses to boot if any
  of them still holds a placeholder value.

**Verdict: correct.** No credential leak path found.

## Conclusion

No code changes were needed for this review — the existing implementation
already enforces signature verification, the live-mode boundary, and
credential handling correctly. The one dormant-dispatch observation above
is worth keeping in mind as a review item for whenever the real dispatch
trigger is actually wired up, not an issue with the code as it exists today.
