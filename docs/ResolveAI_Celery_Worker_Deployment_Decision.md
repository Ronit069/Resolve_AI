# Celery Worker Deployment & Supervision Decision (issue #17)

## Decision: Kubernetes (EKS), two Deployments instead of one

Consistent with issue #14's AWS decision -- "a deployment target
consistent with whatever's chosen for the API" means EKS for both,
`k8s/api-deployment.yaml` included for that consistency even though
this issue's actual scope is the worker. Nothing here was applied
against a real cluster (no credentials from this session; see
`k8s/README.md` and `infra/terraform/README.md` for the same reasoning).

## Why two worker Deployments, not one

`process_evidence_document_task` (PDF/image preprocessing + real
Tesseract OCR, issue #8) has a materially different resource and
latency profile than every other Celery task in this codebase
(enrichment, evidence validation, risk scoring, external actions). On
a single worker process, a slow OCR job blocks everything queued behind
it, and the two workloads can't be scaled independently.

**Fix, in code (not just the deployment manifests):**
`app/worker/celery_app.py` now routes `process_evidence_document_task`
to its own `documents` queue via `task_routes`; every other task stays
on Celery's default `celery` queue. Verified locally that the routing
resolves correctly for every registered task (`test_celery_queue_routing.py`).

A worker with no `-Q` flag only consumes the default queue -- without
also fixing `Dockerfile.worker`'s `CMD` to `-Q celery,documents`, adding
this routing alone would have made `documents`-queue tasks silently
never execute under the existing single-worker `docker-compose.yaml`
setup. Caught and fixed before it shipped.

- `k8s/worker-default-deployment.yaml` -- `-Q celery`, 2-6 replicas,
  light resource requests.
- `k8s/worker-documents-deployment.yaml` -- `-Q documents`, 1-8
  replicas, heavier resource requests (OCR/image processing).

`docker-compose.yaml`'s single `worker` service keeps consuming both
queues (`-Q celery,documents`, via the Dockerfile fix above) -- correct
for one process, just not independently scalable.

## Restart policy

- **Single-host (docker-compose.yaml)**: `restart: unless-stopped` on
  every service (postgres, redis, minio, mlflow, api, worker, web) --
  previously none of them had any restart policy at all, so a crashed
  container during local/demo use just stayed dead.
- **Kubernetes**: a Deployment's pods restart automatically
  (`restartPolicy: Always` is the pod default) -- the actual addition is
  the liveness probe (`celery inspect ping` against the pod's own
  hostname) so Kubernetes can detect a worker process that's still
  running but has stopped actually processing tasks (e.g. a dropped
  broker connection that never recovers), which a bare
  process-exists check would miss.

## Scaling

CPU-based HorizontalPodAutoscalers on both worker Deployments, sized
differently (`worker-documents` scales further, 1-8 vs 2-6, since OCR
load is the spikier of the two). This is a starting point, not the
final word -- see `k8s/README.md`'s "What's still open" for queue-depth
-based autoscaling (KEDA) as the natural next step once there's real
traffic data to size against.

## What's still open

- Applying any of this against a real cluster -- same "only a human
  with account access and budget authority" boundary as issue #14.
- KEDA / queue-depth-based autoscaling (see above).
- A PodDisruptionBudget per Deployment, once there's a real multi-node
  cluster to actually benefit from one.
