terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# RDS for PostgreSQL + pgvector
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "resolve_ai" {
  name       = "resolve-ai-${var.environment}"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "postgres" {
  name_prefix = "resolve-ai-postgres-${var.environment}-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "resolve_ai" {
  identifier     = "resolve-ai-${var.environment}"
  engine         = "postgres"
  engine_version = var.postgres_engine_version
  instance_class = var.postgres_instance_class

  db_name  = var.postgres_database_name
  username = var.postgres_master_username
  password = var.postgres_master_password

  db_subnet_group_name   = aws_db_subnet_group.resolve_ai.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  multi_az               = var.postgres_multi_az

  allocated_storage     = 50
  max_allocated_storage = 200
  storage_encrypted     = true

  # pgvector is in RDS's extension allowlist for this engine version --
  # still run `CREATE EXTENSION IF NOT EXISTS vector;` once after apply
  # (same statement ci.yml / docker-build.yml already run).
  skip_final_snapshot       = false
  final_snapshot_identifier = "resolve-ai-${var.environment}-final"

  tags = {
    Project     = "ResolveAI"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# ElastiCache for Redis (Celery broker + result backend)
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "resolve_ai" {
  name       = "resolve-ai-${var.environment}"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "redis" {
  name_prefix = "resolve-ai-redis-${var.environment}-"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "resolve_ai" {
  replication_group_id = "resolve-ai-${var.environment}"
  description          = "ResolveAI Celery broker/result backend"

  engine             = "redis"
  node_type          = var.redis_node_type
  num_cache_clusters = 2

  subnet_group_name  = aws_elasticache_subnet_group.resolve_ai.name
  security_group_ids = [aws_security_group.redis.id]

  transit_encryption_enabled = true
  automatic_failover_enabled = true

  tags = {
    Project     = "ResolveAI"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# S3 bucket + an IAM role StorageClient can assume instead of static keys
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "resolve_ai" {
  bucket = var.s3_bucket_name

  tags = {
    Project     = "ResolveAI"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "resolve_ai" {
  bucket = aws_s3_bucket.resolve_ai.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "resolve_ai" {
  bucket                  = aws_s3_bucket.resolve_ai.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "app_storage_access" {
  statement {
    sid       = "ObjectAccess"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.resolve_ai.arn}/*"]
  }
  statement {
    sid       = "BucketAccess"
    actions   = ["s3:ListBucket", "s3:CreateBucket", "s3:HeadBucket"]
    resources = [aws_s3_bucket.resolve_ai.arn]
  }
}

resource "aws_iam_policy" "app_storage_access" {
  name   = "resolve-ai-${var.environment}-storage-access"
  policy = data.aws_iam_policy_document.app_storage_access.json
}

# Trust policy is deliberately left generic (ECS tasks.amazonaws.com by
# default below) -- swap for the real runtime (EKS IRSA's OIDC provider,
# or an EC2 instance profile's ec2.amazonaws.com) before applying.
data "aws_iam_policy_document" "app_storage_role_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app_storage_role" {
  name               = "resolve-ai-${var.environment}-storage-role"
  assume_role_policy = data.aws_iam_policy_document.app_storage_role_trust.json
}

resource "aws_iam_role_policy_attachment" "app_storage_role" {
  role       = aws_iam_role.app_storage_role.name
  policy_arn = aws_iam_policy.app_storage_access.arn
}
