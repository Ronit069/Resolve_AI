# Secrets inventory

Every credential/secret `app/core/config.py::Settings` reads from the environment (via `.env` locally, or real environment variables / a secrets manager in any other deployment). `backend/.env` is gitignored and must never be committed — `.env.example` shows the shape, not real values.

Setting `ENVIRONMENT=production` (or `staging`) makes the app refuse to start if any of these still hold an obvious placeholder value (the `sk-dummy`/`gsk_dummy` code defaults, this project's own dev/test values, or an un-edited `your_..._here` from `.env.example`) — see `Settings._reject_placeholder_secrets_outside_dev`. `ENVIRONMENT` is left at its `development` default for local work and CI, where the placeholder values below are intentional.

| Variable | Used for | Where |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_HOST` / `POSTGRES_PORT` | Primary database connection (also used for pgvector-backed RAG tables) | `app/core/database.py` |
| `REDIS_URL` | Celery broker + result backend | `app/worker/celery_app.py` |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_BUCKET_NAME` | Object storage for evidence documents, derived OCR artifacts, and RAG knowledge source text (MinIO locally, real S3 in a real deployment) | `app/core/storage.py`, `app/services/rag/ingestion.py`, `app/services/rag/chunking.py` |
| `API_SECRET_KEY` | **Currently also doubles as the JWT signing secret** for the real-auth login flow (`app/core/security.py`) — rotating it invalidates every issued token | `app/core/security.py` |
| `RAZORPAY_WEBHOOK_SECRET` | HMAC verification of inbound Razorpay webhooks | `app/api/endpoints/webhooks.py`, `app/api/endpoints/outcome_webhooks.py` |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Basic Auth credentials against the real Razorpay API (Module J external actions: document upload, contest submission) | `app/services/external_action/razorpay_client.py` |
| `RAZORPAY_ALLOW_LIVE_MODE` | Not a secret — a fail-closed flag. Must be explicitly `True` for a non-test `RAZORPAY_KEY_ID` to actually be used against the real API | `app/core/config.py`, Module J |
| `OPENAI_API_KEY` | Embeddings for RAG knowledge retrieval (`text-embedding-3-small`) | `app/services/rag/embedding.py` |
| `GROQ_API_KEY` | LLM draft generation (Module G response drafting) | `app/services/rag/generation.py` |
| `CORS_ALLOWED_ORIGIN` | Not a secret — the exact frontend origin allowed to call the API | `app/main.py` |

## What's still missing for a real deployment

This file documents *what* secrets exist and *where* they're consumed — it is not a secrets *manager*. There is currently no mechanism wiring a real deployment to actual secret values beyond `.env`. Before deploying anywhere reachable by real traffic:

1. Pick a secrets backend appropriate to the deploy target (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, or platform-native env injection).
2. Wire the deploy pipeline to inject real values for every row above at deploy/start time — never via a committed file.
3. Set `ENVIRONMENT=production` (or `staging`) so the app refuses to boot if that injection silently failed and left a placeholder behind.
