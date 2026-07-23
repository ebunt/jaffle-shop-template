output "dbt_repository_uri" {
  value = aws_ecr_repository.dbt.repository_url
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.dbt_build.family
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.dbt_build.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.dbt_build.name
}

output "data_bucket_name" {
  value = aws_s3_bucket.data.bucket
}

output "glue_database_name" {
  value = var.glue_database_name
}

output "raw_database_name" {
  value = var.raw_database_name
}

output "athena_workgroup" {
  value = var.athena_workgroup
}

output "task_security_group_id" {
  value = aws_security_group.task.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "task_role_arn" {
  value = aws_iam_role.dbt_task.arn
}

output "task_execution_role_arn" {
  value = aws_iam_role.dbt_task_execution.arn
}

output "scheduler_role_arn" {
  value = aws_iam_role.scheduler.arn
}
