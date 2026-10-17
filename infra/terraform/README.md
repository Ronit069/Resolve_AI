# ResolveAI production infrastructure (reference, unapplied)

Terraform sketch for the three resources `docs/ResolveAI_Production_Infrastructure_Decision.md`
(issue #14) decided on: RDS for PostgreSQL (pgvector), ElastiCache for
Redis, and an S3 bucket + an IAM role `StorageClient` can assume instead
of static keys.

**This has never been run against a real AWS account.** No cloud
credentials are available from the session that wrote it, and applying
infrastructure (and its cost) against someone else's account without
them explicitly provisioning and owning it isn't something to do without
a human driving it. Treat every value in `variables.tf` as a starting
point to review, not a production-ready default — instance sizes,
engine version, and especially `vpc_id`/the subnet list need to match
the account's actual network before `terraform plan` means anything.

## Layout

- `main.tf` — provider + the three resources (RDS, ElastiCache, S3 + IAM role/policy)
- `variables.tf` — everything a reviewer should set per-environment (sizes, network, bucket name)
- `outputs.tf` — the connection values `backend/SECRETS.md`'s env vars need (`POSTGRES_HOST`, `REDIS_URL`, the S3 bucket name/region, the IAM role ARN)

## Using it for real

1. Review every variable default against the target account's actual
   VPC/subnets and budget.
2. `terraform init && terraform plan` against real AWS credentials, read
   the plan carefully, then `terraform apply` if it's correct.
3. Copy `outputs.tf`'s values into the deployment's secrets manager
   (never into a committed `.env`) per `backend/SECRETS.md`.
4. Attach `aws_iam_role.app_storage_role`'s ARN to whatever actually runs
   the API/worker (ECS task role, EKS service account via IRSA, or an
   EC2 instance profile) and leave `S3_ACCESS_KEY`/`S3_SECRET_KEY` unset
   so `StorageClient` uses that role instead of static keys.
