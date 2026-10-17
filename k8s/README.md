# ResolveAI Kubernetes manifests (reference, unapplied)

Issue #17's decision: Kubernetes (on EKS, consistent with issue #14's
AWS decision -- "a deployment target consistent with whatever's chosen
for the API" means the API gets the same target, `api-deployment.yaml`).
**Never run against a real cluster from this session** -- no
credentials were available to do that, same reasoning as
`infra/terraform/`. Review every default (resource requests/limits,
replica counts, probe timings) against real load data before applying.

## Why two worker Deployments, not one

`process_evidence_document_task` (PDF/image preprocessing + real
Tesseract OCR, issue #8) has a completely different resource and
latency profile than every other Celery task here (enrichment,
validation, risk scoring, external actions) -- a slow OCR job must
never block those behind it on the same worker process, and the two
should scale independently. `app/worker/celery_app.py`'s `task_routes`
sends that one task to its own `documents` queue; everything else stays
on Celery's default `celery` queue.

- `worker-default-deployment.yaml` -- `-Q celery`, light resource
  requests, scales 2-6 replicas on CPU.
- `worker-documents-deployment.yaml` -- `-Q documents`, heavier resource
  requests (OCR/image processing), scales 1-8 replicas on CPU.

Both use the same `Dockerfile.worker` image; only the command's `-Q`
flag and the resource profile differ. `docker-compose.yaml`'s single
`worker` service still consumes both queues (`-Q celery,documents`) --
correct for one process, just not independently scalable the way two
Kubernetes Deployments are.

## Restart policy

A crashed worker pod is restarted by Kubernetes itself (Deployments
default to `restartPolicy: Always` for their pods) -- no separate
supervisor needed. The liveness probe below (`celery inspect ping`
against the pod's own hostname) lets Kubernetes detect a worker that's
still running but has stopped actually processing (e.g. a deadlocked
connection) and restart it, which a bare process-exists check would
miss.

## What's still open

- Queue-depth-based autoscaling (KEDA + a Redis/Celery queue-length
  scaler) would react to backlog directly instead of CPU as a proxy for
  it -- the CPU-based HPAs here are a reasonable starting point, not the
  final word, especially for `worker-documents` where OCR load can spike
  without CPU climbing proportionally during I/O-bound phases.
- Secrets: every manifest references a `resolveai-secrets` Secret object
  by name (`POSTGRES_PASSWORD`, `API_SECRET_KEY`, etc.) rather than
  embedding values -- populating that Secret from AWS Secrets Manager
  (e.g. via the External Secrets Operator) is the same
  `backend/SECRETS.md` wiring issue #5 already calls for, just at the
  Kubernetes layer instead of ECS's.
