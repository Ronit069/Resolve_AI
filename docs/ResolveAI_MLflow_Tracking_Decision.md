# MLflow Tracking Strategy Decision (issue #15)

## Decision: keep the frozen PO decision (local/self-hosted only), on SQLite instead of a bare file path

No change to the actual decision `app/services/mlops/mlflow_tracking.py`'s
docstring already recorded: no external/hosted MLflow service, no
production credential. What changed is the storage format underneath
that decision, and which training script actually uses it.

## What this review found

1. **The local file-store default was silently broken.** `mlflow>=2.14.0`
   (`requirements.txt`'s own floor) resolves to MLflow 3.x today, which
   put the bare-filesystem tracking backend (`MLFLOW_TRACKING_URI=./mlruns`,
   the old default) into maintenance mode — it now raises
   `MlflowException` instead of working. `log_training_run`'s own
   best-effort `try`/`except` swallowed that exception exactly as
   designed (a training run always succeeds whether or not MLflow is
   reachable), which is precisely why this went unnoticed: every run
   kept succeeding, it just never actually got logged anywhere.

   **Fix**: `MLFLOW_TRACKING_URI` now defaults to `sqlite:///mlflow.db` —
   confirmed working directly against the installed mlflow 3.17.1 with no
   server process required, same "local/self-hosted only" guarantee (no
   external service, no production credential), not deprecated. The
   `docker-compose.yaml` `mlflow` service's `--backend-store-uri` was
   bumped from `/mlruns` to `sqlite:////mlruns/mlflow.db` for the same
   reason — a *running* `mlflow server` using the bare-path backend would
   hit the identical deprecation once its image's bundled mlflow is 3.x.
   `--default-artifact-root` stays a plain path: only the backend *store*
   (run metadata) was deprecated, never the artifact store.

2. **The real champion pipeline never logged to MLflow at all.**
   `log_training_run` was only ever called from
   `train_lightgbm_comparator.py` — a comparator/alternative model.
   `train_benchmark.py`, the script that actually produces what
   `promote_champion.py` promotes to champion, never called it. MLflow
   therefore had a record of a model that never ships, and no record of
   the one that does — exactly the "two disconnected sources of truth"
   risk this issue named, except it wasn't a future risk, it was already
   true.

   **Fix**: added the same best-effort `log_training_run` call to
   `train_benchmark.py`, tagging `model_artifact_sha256` with the exact
   same `model_cbm_sha256` its own `metadata.json` already computes.

## Why `model_artifact_sha256` is the join key, not a new ID

`promote_candidate_from_disk` (issue #6/#PR2) sets
`ModelVersion.preprocessing_hash = candidate.model_sha256` — the same
sha256 of `model.cbm` that `train_benchmark.py`'s `metadata.json` and now
its MLflow run both carry. A promoted model's MLflow run is found with
one query (`tags.model_artifact_sha256 == <ModelVersion.preprocessing_hash>`)
instead of needing a dedicated new identifier management for one more
record to possibly drift from the other two. Verified locally: a real
training run's MLflow tag and its `metadata.json`'s
`artifacts.model_cbm_sha256` matched exactly.

## What's still open

- MLflow here answers "what happened during training" (params, metrics,
  dataset hashes) — it is not consulted anywhere a production decision
  gets made; `promote_champion.py`'s guardrail compares `metadata.json`/
  the Step 15 holdout artifact directly, unchanged by this issue. That
  division of responsibility is intentional and isn't being revisited
  here.
- If this project ever needs MLflow visible outside one machine (today
  it's either direct-file-local or the docker-compose service on the
  same host), that's the same local-disk-only distribution gap issue #6
  solved for the model/calibrator specifically — a managed MLflow
  tracking server would be the natural next step, out of scope for this
  issue's "local/self-hosted only" decision.
