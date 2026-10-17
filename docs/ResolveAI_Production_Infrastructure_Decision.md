# Production Infrastructure Decision (issue #14)

## Scope

Decide what real infrastructure a deployed ResolveAI backend runs
against, replacing the local-only stack every test and manual
verification in this project's history has used: a local Postgres with
pgvector installed by hand, a local `redis-server`, and a `moto.server`/
MinIO S3 mock. This is a decision + config document, not an actual
provisioning action — nothing here was run against a real cloud account
(no credentials are available to do that from this session, and standing
up real billed infrastructure isn't something to do without a human
explicitly provisioning and owning the account).

## Decision: AWS

`app/core/storage.py::StorageClient` already speaks the S3 API via
`boto3` (MinIO locally is deliberately S3-API-compatible), and
`app/core/config.py` already reads plain `POSTGRES_*`/`REDIS_URL`/`S3_*`
connection settings with no vendor-specific code anywhere. AWS is the
natural fit for exactly the reason the issue names: this should be a
config/credential change, not a code change. (GCP/Azure equivalents --
Cloud SQL+pgvector, Memorystore, Cloud Storage; Azure Database for
PostgreSQL, Azure Cache for Redis, Blob Storage -- would work under the
same config-only model if a different cloud is chosen later; nothing
below is AWS-specific at the application layer.)

## Postgres: Amazon RDS for PostgreSQL

- Engine version **16.x**, matching `pgvector/pgvector:pg16` in
  `.github/workflows/ci.yml` and `docker-compose.yaml` (issue #13 fixed
  the latter to match -- previously it ran pg15, silently untested
  against the same engine version CI and migrations actually validate).
- `pgvector` is available as a standard RDS extension on PostgreSQL 15.2+;
  enable it once via `CREATE EXTENSION IF NOT EXISTS vector;` after the
  instance is up (the same statement `ci.yml`'s "Enable pgvector
  extension" step and `docker-build.yml`'s dry run already run).
- Multi-AZ for production; a single-AZ instance is fine for a staging
  environment.
- `KnowledgeChunk.embedding` (Module G RAG retrieval) is the only
  pgvector-dependent column -- confirm the chosen instance class has
  enough memory for the HNSW index at the knowledge base's real size
  before relying on it under load.
- **Config change only**: point `POSTGRES_HOST`/`POSTGRES_PORT`/
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` at the RDS endpoint.
  `app/core/database.py`'s `DATABASE_URL` property needs nothing else.

## Redis: Amazon ElastiCache for Redis

- Used only as the Celery broker + result backend
  (`app/worker/celery_app.py`) -- no durability requirement beyond "a
  task in flight survives a broker restart," so cluster mode disabled
  (a single primary + replica) is sufficient to start; revisit if task
  throughput grows enough to need sharding.
- **Config change only**: point `REDIS_URL` at the ElastiCache endpoint
  (`rediss://` if TLS-enabled, which AWS recommends).

## S3: a real bucket, IAM-role credentials (not static keys)

- Create a real S3 bucket in the account's actual region.
- Issue #14's review of `StorageClient` found it hardcoded
  `region_name='us-east-1'` unconditionally and *required*
  `S3_ACCESS_KEY`/`S3_SECRET_KEY` -- both wrong for a real AWS deployment
  outside `us-east-1`, and both push toward long-lived static keys where
  AWS's own guidance is an IAM role. Fixed in this PR:
  - Added `S3_REGION` (defaults to `us-east-1`, so MinIO/local dev/CI are
    unaffected); set it to the bucket's real region in production.
  - `S3_ACCESS_KEY`/`S3_SECRET_KEY` are now optional. MinIO still needs
    them (it has no IAM concept), but a real deployment can leave both
    unset and run on the ECS task role / EKS IRSA role / EC2 instance
    profile attached to the running process -- `StorageClient` only
    passes static credentials to `boto3.client` when both are set,
    otherwise boto3's own default credential chain (the IAM role) takes
    over, exactly as AWS recommends over static keys.
  - Grant that role least-privilege S3 access scoped to the one bucket
    (`s3:GetObject`/`PutObject`/`DeleteObject`/`HeadBucket` on
    `arn:aws:s3:::<bucket>/*` plus `s3:ListBucket`/`CreateBucket` on the
    bucket ARN itself -- `StorageClient._ensure_bucket_exists` calls
    `head_bucket` and, if missing, `create_bucket`).

## Secrets wiring

Ties directly into `backend/SECRETS.md` (issue #5): none of
`POSTGRES_PASSWORD`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` (if a deployment
still chooses static S3 keys)/etc. should reach these services via a
committed file. Inject real values at deploy time from AWS Secrets
Manager or Parameter Store, and set `ENVIRONMENT=production` so
`Settings._reject_placeholder_secrets_outside_dev` refuses to boot if
that injection silently left a placeholder behind.

## Reference-only Terraform skeleton

`infra/terraform/` sketches the three resources above (RDS, ElastiCache,
S3 + the IAM role `StorageClient` can assume) as a starting point for
whoever actually provisions this. It is **not validated against a real
AWS account from this session** (no credentials were available, and
`terraform apply` against a stranger's cloud account/budget without
explicit authorization is exactly the kind of action this project's own
engineering norms rule out) -- review every default (instance sizes,
engine version, VPC/subnet wiring to the account's existing network)
before running `terraform plan` against a real account.

## What's still open after this issue

- Actually running `terraform apply` (or the account owner's preferred
  IaC) against a real AWS account -- this is the one piece only a human
  with account access and budget authority can do.
- Deciding whether a non-`us-east-1` region changes any other
  AWS-service-specific assumption elsewhere in the codebase (none found
  in this review beyond the `StorageClient` region fix above).
