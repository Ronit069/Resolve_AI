variable "aws_region" {
  description = "AWS region for every resource below. Must match S3_REGION in backend/SECRETS.md."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name, used in resource naming and tags."
  type        = string
  default     = "production"
}

variable "vpc_id" {
  description = "VPC to launch RDS/ElastiCache into. REVIEW BEFORE USE -- there is no safe default."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs (at least 2, in different AZs) for the RDS/ElastiCache subnet groups. REVIEW BEFORE USE."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups (the API/worker's own) allowed to reach Postgres (5432) and Redis (6379)."
  type        = list(string)
}

variable "postgres_engine_version" {
  description = "Matches pgvector/pgvector:pg16 in docker-compose.yaml and ci.yml -- keep these in sync."
  type        = string
  default     = "16.4"
}

variable "postgres_instance_class" {
  description = "Starting point only -- size to the real knowledge-base/case volume before trusting this in production."
  type        = string
  default     = "db.t4g.medium"
}

variable "postgres_multi_az" {
  description = "Multi-AZ failover. true for production, false is acceptable for staging."
  type        = bool
  default     = true
}

variable "postgres_database_name" {
  type    = string
  default = "resolve_db"
}

variable "postgres_master_username" {
  type    = string
  default = "resolve_user"
}

variable "postgres_master_password" {
  description = "Set via TF_VAR_postgres_master_password or a Terraform Cloud/CI secret -- never commit a real value."
  type        = string
  sensitive   = true
}

variable "redis_node_type" {
  description = "Starting point only -- Celery broker/result-backend load is the only driver; re-size once there's real throughput data."
  type        = string
  default     = "cache.t4g.small"
}

variable "s3_bucket_name" {
  description = "Must be globally unique. Matches S3_BUCKET_NAME in backend/SECRETS.md."
  type        = string
}
