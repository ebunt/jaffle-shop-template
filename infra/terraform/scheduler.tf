resource "aws_scheduler_schedule" "daily_dbt_build" {
  name       = "${var.name_prefix}-daily-dbt-build"
  state      = var.schedule_state
  group_name = "default"

  schedule_expression          = var.daily_schedule_cron
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.dbt_build.arn
      launch_type         = "FARGATE"
      task_count          = 1

      network_configuration {
        subnets          = aws_subnet.public[*].id
        security_groups  = [aws_security_group.task.id]
        assign_public_ip = true
      }
    }
  }
}
