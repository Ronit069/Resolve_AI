output "postgres_host" {
  description = "-> POSTGRES_HOST"
  value       = aws_db_instance.resolve_ai.address
}

output "postgres_port" {
  description = "-> POSTGRES_PORT"
  value       = aws_db_instance.resolve_ai.port
}

output "redis_url" {
  description = "-> REDIS_URL (TLS-enabled, use rediss://)"
  value       = "rediss://${aws_elasticache_replication_group.resolve_ai.primary_endpoint_address}:6379/0"
  sensitive   = false
}

output "s3_bucket_name" {
  description = "-> S3_BUCKET_NAME"
  value       = aws_s3_bucket.resolve_ai.bucket
}

output "s3_region" {
  description = "-> S3_REGION"
  value       = var.aws_region
}

output "app_storage_role_arn" {
  description = "Attach this role to whatever runs the API/worker (ECS task role, EKS service account via IRSA, or EC2 instance profile) and leave S3_ACCESS_KEY/S3_SECRET_KEY unset."
  value       = aws_iam_role.app_storage_role.arn
}
