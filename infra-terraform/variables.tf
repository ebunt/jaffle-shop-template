variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

# "tf" so this stack's resources coexist in the same account as the CDK
# stack (infra/) and the Pulumi stack (infra-pulumi/) without colliding on
# account-unique names (IAM role/policy names, the S3 bucket).
variable "name_prefix" {
  description = "Prefix for all named resources in this stack."
  type        = string
  default     = "jaffle-shop-tf"
}

# Both databases are pre-existing in this account (from prior dbt Cloud
# runs) -- this stack only references them by name via IAM, same as the
# CDK stack. See infra/jaffle_shop_infra/stack.py for the full rationale.
variable "glue_database_name" {
  type    = string
  default = "jaffle_shop"
}

variable "raw_database_name" {
  type    = string
  default = "raw"
}

variable "athena_workgroup" {
  type    = string
  default = "primary"
}

variable "daily_schedule_cron" {
  description = "EventBridge Scheduler cron expression."
  type        = string
  default     = "cron(0 6 * * ? *)" # 06:00 UTC daily
}

# See stack.py's SCHEDULE_STATE comment: CloudFormation/Terraform re-applies
# this value on every apply that touches the schedule, so toggle it here,
# not via the console/CLI.
variable "schedule_state" {
  type    = string
  default = "DISABLED"
}

variable "gen_years" {
  description = "Years of synthetic data jafgen generates. See stack.py's GEN_YEARS comment re: the OOM at YEARS=6."
  type        = string
  default     = "1"
}

variable "task_cpu" {
  type    = number
  default = 1024
}

variable "task_memory" {
  type    = number
  default = 2048
}
