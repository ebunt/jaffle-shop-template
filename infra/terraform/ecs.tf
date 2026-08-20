resource "aws_ecs_cluster" "this" {
  name = "${var.name_prefix}-cluster"
}

resource "aws_cloudwatch_log_group" "dbt_build" {
  name              = "/${var.name_prefix}/dbt-build"
  retention_in_days = 14
}

resource "aws_security_group" "task" {
  name        = "${var.name_prefix}-task-sg"
  description = "dbt build Fargate task -- outbound only, no inbound"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# family is set explicitly (not left to Terraform's auto-generated default,
# which would just be the resource's Terraform name) so it's identifiable
# and so scheduler_run_task's IAM policy above can reference it directly by
# ARN instead of a Terraform-computed value.
resource "aws_ecs_task_definition" "dbt_build" {
  family                   = "${var.name_prefix}-dbt-build"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.dbt_task_execution.arn
  task_role_arn            = aws_iam_role.dbt_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "DbtBuild"
    image     = docker_registry_image.dbt.name
    essential = true
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.dbt_build.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "dbt"
      }
    }
    environment = [
      { name = "DBT_TARGET", value = "prod" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "DBT_ATHENA_BUCKET", value = aws_s3_bucket.data.bucket },
      { name = "DBT_ATHENA_SCHEMA", value = var.glue_database_name },
      { name = "DBT_ATHENA_WORKGROUP", value = var.athena_workgroup },
      { name = "YEARS", value = var.gen_years },
    ]
  }])
}
