# Three roles, deliberately separate and least-privilege -- see ROLES.md at
# the repo root for the full rationale (written against the CDK stack, but
# the same three-role split applies here unchanged).

# ---- task role: what the running dbt process can do ----

resource "aws_iam_role" "dbt_task" {
  name = "${var.name_prefix}-dbt-task-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

# CDK's data_bucket.grant_read_write(task_role) computes this exact action
# list; Terraform has no equivalent grant() helper, so it's spelled out by
# hand here.
resource "aws_iam_policy" "dbt_task_s3_access" {
  name = "${var.name_prefix}-dbt-task-s3-access"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject*", "s3:GetBucket*", "s3:List*",
        "s3:DeleteObject*", "s3:PutObject", "s3:PutObjectLegalHold",
        "s3:PutObjectRetention", "s3:PutObjectTagging",
        "s3:PutObjectVersionTagging", "s3:Abort*",
      ]
      Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    }]
  })
}

resource "aws_iam_policy" "dbt_task_glue_access" {
  name = "${var.name_prefix}-dbt-task-glue-access"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
        "glue:GetTableVersion", "glue:GetTableVersions", "glue:GetPartition",
        "glue:GetPartitions", "glue:BatchGetPartition", "glue:CreateTable",
        "glue:UpdateTable", "glue:DeleteTable", "glue:BatchCreatePartition",
        "glue:BatchDeletePartition", "glue:BatchDeleteTable",
      ]
      Resource = [
        "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
        "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${var.glue_database_name}",
        "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${var.raw_database_name}",
        "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.glue_database_name}/*",
        "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.raw_database_name}/*",
      ]
    }]
  })
}

resource "aws_iam_policy" "dbt_task_athena_access" {
  name = "${var.name_prefix}-dbt-task-athena-access"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "athena:StartQueryExecution", "athena:GetQueryExecution",
        "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup",
      ]
      Resource = "arn:${data.aws_partition.current.partition}:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dbt_task_s3" {
  role       = aws_iam_role.dbt_task.name
  policy_arn = aws_iam_policy.dbt_task_s3_access.arn
}

resource "aws_iam_role_policy_attachment" "dbt_task_glue" {
  role       = aws_iam_role.dbt_task.name
  policy_arn = aws_iam_policy.dbt_task_glue_access.arn
}

resource "aws_iam_role_policy_attachment" "dbt_task_athena" {
  role       = aws_iam_role.dbt_task.name
  policy_arn = aws_iam_policy.dbt_task_athena_access.arn
}

# ---- execution role: what ECS itself needs to start the container ----

resource "aws_iam_role" "dbt_task_execution" {
  name = "${var.name_prefix}-dbt-task-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "dbt_task_execution_access" {
  name = "${var.name_prefix}-dbt-task-execution-access"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = aws_ecr_repository.dbt.arn
      },
      {
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = aws_cloudwatch_log_group.dbt_build.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "dbt_task_execution" {
  role       = aws_iam_role.dbt_task_execution.name
  policy_arn = aws_iam_policy.dbt_task_execution_access.arn
}

# ---- scheduler role: what EventBridge Scheduler needs to start the task ----

resource "aws_iam_role" "scheduler" {
  name = "${var.name_prefix}-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_iam_policy" "scheduler_run_task" {
  name = "${var.name_prefix}-scheduler-run-task"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecs:RunTask"
        Resource = "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${var.name_prefix}-dbt-build:*"
      },
      {
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = [aws_iam_role.dbt_task.arn, aws_iam_role.dbt_task_execution.arn]
        Condition = { StringLike = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "scheduler_run_task" {
  role       = aws_iam_role.scheduler.name
  policy_arn = aws_iam_policy.scheduler_run_task.arn
}
