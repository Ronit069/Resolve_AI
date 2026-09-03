# ResolveAI

An AI-assisted payment dispute (chargeback) triage, evidence-assembly, and contest-drafting system for merchants on Razorpay. ResolveAI ingests dispute webhooks, validates and scores evidence, drafts a contest response with an LLM (with guardrails), routes cases through human review, and — once approved — submits the contest to Razorpay and tracks the outcome.

## Architecture

```
                     ┌──────────┐        ┌──────────┐
   Razorpay webhooks │   api    │  Celery│  worker  │
  ──────────────────▶│ (FastAPI)│◀──────▶│ (Celery) │
                      └────┬─────┘        └────┬─────┘
                           │                    │
              ┌────────────┼────────────────────┤
              ▼            ▼                    ▼
         ┌─────────┐  ┌─────────┐          ┌─────────┐
         │postgres │  │  redis  │          │  minio  │
         │(pgvector)│  │ (broker)│          │  (S3)   │
         └─────────┘  └─────────┘          └─────────┘

         ┌─────────┐                       ┌─────────┐
         │   web   │──── browser ────▶ api  │ mlflow  │ (optional, local)
         │ (nginx) │                       │(tracking)│
         └─────────┘                       └─────────┘
```

- **api** — FastAPI backend (`backend/app/main.py`): webhook ingestion, evidence upload, review-queue, draft generation, external-action (Razorpay contest) endpoints.
- **worker** — Celery worker (`backend/app/worker/`): dispute enrichment, document OCR/extraction, malware scanning, external-action dispatch.
- **web** — the Module I frontend (`frontend/`): review queue, case workspace, audit log, model metrics.
- **postgres** — pgvector-enabled Postgres (case/dispute/evidence/review/model-registry data + Module G's knowledge-base embeddings).
- **redis** — Celery broker/result backend.
- **minio** — S3-compatible object storage for evidence documents/OCR artifacts.
- **mlflow** — optional, local experiment tracker for the model-training scripts (see "MLflow" below). Never a production/hosted dependency.

## Prerequisites

- Docker and Docker Compose v2 (`docker compose version`).
- A `backend/.env` file (copy `backend/.env.example` and fill in real values — see that file for every required setting: Postgres/Redis/MinIO credentials, `API_SECRET_KEY`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`, `OPENAI_API_KEY`). Never commit `.env`.

## One-command startup

```bash
docker compose up --build
```

This brings up `postgres`, `redis`, `minio`, `mlflow`, `api` (http://localhost:8000), `worker`, and `web` (http://localhost:5173). `api`/`worker` wait on `postgres`'s and `minio`'s healthchecks before starting.

## Initialization / seed command

Run once after the stack is up (safe to re-run — every step is idempotent):

```bash
docker compose run --rm api python scripts/seed_demo.py
```

This applies all Alembic migrations, then seeds:
- a demo merchant
- one demo `AppUser` per role (`MERCHANT_ADMIN`, `RISK_ANALYST`, `APPROVER`, `MODEL_MAINTAINER`) — their generated UUIDs are the `X-User-Id` values the frontend's dev identity selector expects (see the printed output, or query `app_users`)
- a demo reason-code policy (Module E)
- one demo case/dispute, ingested through the same canonical `process_dispute_event` path Razorpay's real webhook uses
- a champion model registration (see "Model registry" below)

## Service URLs

| Service | URL | Notes |
|---|---|---|
| web (frontend) | http://localhost:5173 | Module I review UI |
| api | http://localhost:8000 | `GET /health` for a liveness check |
| api docs | http://localhost:8000/docs | FastAPI's auto-generated Swagger UI |
| mlflow | http://localhost:5001 | Local experiment tracking UI (optional) |
| minio console | http://localhost:9001 | Object storage browser (`minio_admin`/`minio_password` by default) |
| postgres | localhost:5433 | Mapped from the container's 5432 |

## Local development flow (without Docker)

The full stack can also run natively for faster iteration — this is how the existing test suite (`backend/tests/`) and `backend/README`-less dev workflow already operate:

```bash
# Postgres/Redis/MinIO — run locally or via `docker compose up postgres redis minio`
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload          # API
celery -A app.worker.celery_app worker --loglevel=info   # worker, separate shell

cd ../frontend
npm install
npm run dev                            # Vite dev server, http://localhost:5173
```

Run the backend test suite with `pytest` (from `backend/`) and the frontend suite with `npm test` (from `frontend/`).

## MLflow (experiment tracking)

Module F's training scripts (`backend/train_lightgbm_comparator.py` and friends) log their existing params/metrics/dataset hashes to MLflow as a best-effort, additive step — a training run's actual computed result is identical whether or not MLflow is reachable (see `backend/app/services/mlops/mlflow_tracking.py`). By default `MLFLOW_TRACKING_URI` is a local file store (`./mlruns`); when running via `docker compose up`, the `api`/`worker` services point at the local `mlflow` compose service instead. There is no external/hosted MLflow and no production credential involved — this is a local/demo deployment, per the project's own hackathon framing.

To run a training script against the compose `mlflow` service:

```bash
MLFLOW_TRACKING_URI=http://localhost:5001 python backend/train_lightgbm_comparator.py
```

Then browse runs at http://localhost:5001.

### Model registry / champion model

`ModelVersion` (`backend/app/models/module_f.py`) is the registry's existing table. "Champion" is a convention on its existing free-text `status` column (`"champion"` / `"retired"`) — see `backend/app/services/mlops/model_registry.py` — not a new database column or migration. `scripts/seed_demo.py` registers a champion automatically if none exists.

## Runtime observability

- `GET /api/v1/observability/queue-metrics` — H-22's human-review-queue metrics (queue age, near-deadline, review turnaround). Role-gated.
- `GET /api/v1/observability/runtime-metrics` — Module L's runtime/infra metrics (OCR latency, LLM latency, Celery task duration, error rate), kept intentionally separate from H-22. In-process/in-memory only — resets on process restart. Role-gated.

## Dataset limitations

Module F's risk model is trained on a **synthetic** benchmark dataset (see `backend/app/services/ml/synthetic_benchmark.py` and the training scripts at the repo root) — there is no real Razorpay dispute-outcome dataset available for this project. Metrics reported by the training scripts and `ModelMetric` rows should be read as a demonstration of the pipeline's correctness, not as a claim about real-world dispute-win-rate performance. `TEST_HOLDOUT` data is never used for training or threshold selection (enforced in code — see `load_data`'s explicit guard in the training scripts).

## Demo steps

1. `docker compose up --build`, then run the seed command above.
2. Open http://localhost:5173, sign in via the dev identity selector using one of the seeded `AppUser` UUIDs (see the seed command's printed output).
3. View the seeded demo case in the review queue, open its case workspace, and review the audit log.
4. Check `GET /api/v1/observability/queue-metrics` and `GET /api/v1/observability/runtime-metrics` for the two distinct observability surfaces.
