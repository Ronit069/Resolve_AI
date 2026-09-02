# Module L — Deployment, MLOps & Observability Hardening

Status: DRAFT — for Product Owner review. Not authorized for implementation.
Continuation note: this letter continues the repository's own A-H sequence. It does **not** correspond to the master `ResolveAI_Implementation_Blueprint.md`'s own "Module L — Explainability," which is already implemented inside the repository's Module F (SHAP-based `PredictionExplanation`). This module instead closes the gap between the backend's own passing test suite and the master blueprint's definition of a complete, reproducible hackathon deliverable. See `ResolveAI_Next_Four_Modules_Research_Report.md` §2, §7.

## 1. Objective

Make the existing, already-implemented Modules A-H (and, once built, I/J/K) reproducibly deployable with one command, and give the project the experiment-tracking/model-registry and written documentation the master blueprint's own Definition of Done requires but that does not currently exist.

## 2. Scope

**Module boundary (reaffirmed per PO decision):** Deployment, MLOps, and Observability hardening are intentionally **one combined module**, not three. A split into separate "Deployment" and "MLOps/Observability" modules was explicitly considered (see the reconciliation report's own §9, which flagged this as the one genuinely open splitting question in the post-H roadmap) and was **rejected by the PO** — this module remains combined. The rationale for keeping them together: both halves package and instrument the same already-implemented Modules A-H (plus, eventually, I/J/K) for the same end goal — a reproducible, observable deployment of the whole system — and neither half has any function without the other in a hackathon-scale deliverable (a deployment with no experiment tracking/model registry is not reproducible in the sense master §13 requires; experiment tracking with no deployment path is not usable by anyone but the ML author).

In scope: `Dockerfile`s for the API and worker services, an extended `docker-compose.yaml`, experiment tracking/model registry wiring for Module F's existing training artifacts, a written `README.md`, and seed/smoke-test scripts — **and** runtime/operational observability distinct from H-22 (see §12). Out of scope: any change to Module F's actual training/evaluation logic, or to any Module A-H business logic.

## 3. Authoritative Requirements

| ID (this draft) | Requirement | Source |
|---|---|---|
| L-01 | Experiment tracking: MLflow or structured runs directory containing params, metrics, split hash and artifact URI | Master blueprint §13 |
| L-02 | Model registry: `model_versions` table + artifact directory; mark champion model explicitly | §13 (table already exists — `app/models/module_f.py::ModelVersion` — this module adds the artifact/registry wiring around it) |
| L-03 | Data/version trace: dataset hash, split manifest, feature version and policy version | §13 |
| L-04 | Runtime metrics: inference latency, OCR latency, LLM latency, queue duration, error rate | §13 (distinct from H-22's review-queue observability, which is already implemented) |
| L-05 | `web`, `api`, `worker`, `postgres`, `redis`, `minio`, optional `mlflow` Compose services | §20, §20.1 |
| L-06 | Provide seed fixtures and one command such as `docker compose up --build`; a separate initialization command applies migrations, seeds reason-code policies, creates demo users/cases, registers the chosen model artifact | §20.1 |
| L-07 | Docker-based reproducible startup works | §22 Final Submission Checklist |
| L-08 | README explains setup, architecture, dataset limitations and demo steps | §22 |
| L-09 | One-command local launch, seed data, model artifact, README and smoke tests | §15 "Recommended Implementation Sequence," P2-5 |

## 4. Functional Requirements

- Add `Dockerfile`s for the FastAPI `api` service and the Celery `worker` service (both already exist as code — `app/main.py`, `app/worker/tasks.py` — just not containerized).
- Extend the existing `docker-compose.yaml` (currently `postgres`, `redis`, `minio` only) to add `api`, `worker`, and — once Module I exists — `web`, plus an optional `mlflow` service, per L-05, without altering the existing infra service definitions.
- Add an initialization/seed command that runs Alembic migrations, seeds reason-code policies (Module E) and demo users/cases, and registers a chosen `ModelVersion` as champion (L-06) — this reuses existing seed/dev-endpoint patterns (`app/api/endpoints/dev.py`) rather than inventing new demo-data logic.
- Wire experiment tracking (L-01) around Module F's existing ad-hoc training scripts (`train_lightgbm_comparator.py`, `calibrate_and_optimize.py`, `evaluate_baseline.py`, etc., at the repo root) — logging their existing params/metrics/artifacts to MLflow or a structured runs directory, per the PO's choice (§18) — without changing what those scripts actually compute.
- Write `README.md` (currently zero bytes) covering setup, architecture, dataset limitations (real vs. synthetic, per master §10, already documented in Module F's own synthetic-benchmark work), and demo steps (master §18's three-case demo flow).
- Add smoke tests that exercise the one-command startup path end-to-end (e.g., the container stack comes up healthy, migrations apply, a seeded demo case can be fetched via the API) — distinct from and not a replacement for the existing pytest suite.

## 5. Data Model

- **Existing tables/models**: `ModelVersion`, `ModelDecisionPolicy` (Module F) — already the registry's core.
- **Required schema changes**: none anticipated for L-01/L-02/L-03 (MLflow/structured-runs tracking lives outside PostgreSQL, alongside the existing artifact directory convention). If a "champion model" flag is desired as a queryable DB column rather than convention/config, that is a small, explicitly-scoped, separately-authorized addition — not assumed here.
- **New tables**: none required by this draft.
- Do not invent persistence: this module is infrastructure/tooling, not a new business-data owner.

## 6. API / Service Contracts

This module is primarily infrastructure, not new API surface. The one plausible new contract:

| Contract | Direction | Input | Output | Authorization | Notes |
|---|---|---|---|---|---|
| `GET /api/v1/health` variants for each container (already exists: `GET /health` in `app/main.py`) | internal | none | status | none (already unauthenticated, matches existing convention) | Reused as-is for Docker healthchecks (mirroring the existing `postgres`/`minio` healthcheck pattern in `docker-compose.yaml`) |

No new business-logic endpoint is proposed by this module.

## 7. Security / Authorization

No new authorization surface. Secrets (Razorpay credentials once Module J exists, DB/Redis/MinIO credentials already in `.env`) must continue to come from environment/secret configuration per the existing convention (`app/core/config.py`, `Settings`) — this module's Compose/Docker work must not hard-code any credential into an image or committed file (master SH-10, §12).

## 8. State / Workflow Rules

Not applicable — this module has no case/business state of its own.

## 9. Auditability

Not applicable in the Module A-H sense; experiment-tracking metadata (L-01/L-03) is itself a form of auditability for the ML side, already partially named as a requirement by the master blueprint's MLOps section.

## 10. Idempotency / Concurrency

The seed/initialization command (L-06) must be safe to re-run (idempotent) against an already-seeded database — reuse existing unique-constraint-driven idempotency patterns (e.g., `Merchant.external_merchant_id`, `WebhookEvent.external_event_id`) rather than requiring a manual "clean slate" each time.

## 11. External Integrations

None required. MLflow, if chosen (§18), is a self-hosted/local dependency, not a third-party API contract with the same risk profile as Module J's Razorpay integration.

## 12. Observability

This module's L-04 (runtime metrics: inference/OCR/LLM latency, queue duration, error rate) is distinct from — and currently entirely separate from — the existing H-22 observability endpoint, which covers only human-review-queue metrics (queue age, near-deadline, review turnaround, and eventually submission success/API errors once Module J exists). L-04 is a genuinely new, unbuilt observability surface (runtime/infra metrics, not review-queue metrics) with zero existing implementation to reuse; this must not be casually merged into `app/services/observability/queue_metrics.py`, which is H-22's frozen, additive-only surface. This distinction is precisely why L-04 and H-22 stay conceptually separate concerns *within* one module rather than an argument for splitting this module in two: L-04 lives here (alongside deployment/experiment-tracking) because it is genuinely new, unowned infrastructure work (per the reconciliation report §5), not because it has anything functionally to do with Dockerfiles or MLflow — the module boundary is "infrastructure the product needs but no pipeline stage owns," not "things that are similar to each other."

## 13. Testing Requirements

(For the eventual implementation plan.) Smoke tests for the one-command startup path (§4); tests that the seed command is idempotent; tests that no credential leaks into a built image (e.g., an image-layer secret scan); explicit confirmation that Dockerizing the API/worker does not change their runtime behavior (same app code, same tests passing inside the container as outside it).

## 14. Migration Requirements

None anticipated. This module packages and documents existing schema/migrations; it does not add new ones (unless the optional "champion model" DB flag from §5 is separately authorized).

## 15. Non-Goals

This module must NOT:
- Split into separate Deployment and MLOps/Observability modules — the PO has explicitly considered and rejected this split (§2); Deployment, MLOps, and Observability hardening remain one combined module.
- Redesign, duplicate, or fold H-22's human-review-queue observability (`app/services/observability/queue_metrics.py`, queue age/near-deadline/review-turnaround) into this module's own L-04 runtime-metrics surface, or vice versa — the two remain frozen and distinct per §12, even though both live under this one module's scope.
- Change Module F's actual training/evaluation/calibration logic — only add tracking/logging around it.
- "Clean up" or consolidate the existing ad-hoc training scripts at the repo root beyond what's needed to log to the chosen experiment tracker — broader refactoring is explicitly out of scope (would risk "improving" a frozen module).
- Modify Modules A-H's business logic, schema, or tests.
- Modify TEST_HOLDOUT.
- Introduce a new authentication/authorization mechanism (out of this module's scope; see Module I §18 if real auth is ever built).
- Stand up a live-credentialed `mlflow`/deployment target with production secrets — this remains a local/demo deployment blueprint per the source material's own hackathon framing.

## 16. Dependencies

- Depends on: Modules A-H (packages what already exists); benefits from, but does not require, Modules I/J/K to exist first (can package a partial stack and be extended incrementally).
- Depended on by: none of I/J/K structurally require this module to exist first — purely a packaging/documentation dependency in the other direction.

## 17. Acceptance Criteria

- `docker compose up --build` brings up a healthy stack including `api` and `worker` (and `web`, once Module I exists) alongside the existing `postgres`/`redis`/`minio` services.
- A separate, idempotent initialization command applies migrations and seeds demo data reproducibly.
- `README.md` is non-empty and covers setup, architecture, dataset limitations, and demo steps.
- Module F's existing training runs are logged to the chosen experiment tracker without any change to their computed results.

## 18. Open PO Decisions

1. MLflow vs. the master-blueprint-sanctioned lighter alternative ("structured runs directory") for L-01/L-02/L-03.
2. How much of the existing ad-hoc training scripts to touch (logging calls only, vs. a larger consolidation) — this draft recommends the minimal option to avoid "improving" Module F.
3. Whether the optional "champion model" flag becomes a DB column (small migration) or stays a config/artifact-directory convention.
4. Timing: build this module now (packaging A-H alone) or wait until I/J/K exist for a more complete one-command demo — Research Report §8 recommends starting in parallel with Module I, but this is a PO sequencing choice.
