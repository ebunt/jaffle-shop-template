"""Pulumi (Python) rebuild of infra/'s CDK stack, for comparison.

Mirrors infra/jaffle_shop_infra/stack.py resource-for-resource. See
infra-pulumi/README.md for what differs and why.
"""

import json

import pulumi
import pulumi_aws as aws
import pulumi_docker_build as docker_build

config = pulumi.Config()
aws_config = pulumi.Config("aws")

# "pulumi" so this stack's resources coexist in the same account as the CDK
# stack (infra/) and the Terraform stack (infra-terraform/) without
# colliding on account-unique names (IAM role/policy names, the S3 bucket).
NAME_PREFIX = config.get("namePrefix") or "jaffle-shop-pulumi"
AWS_REGION = aws_config.get("region") or "us-east-1"

# Both databases are pre-existing in this account (from prior dbt Cloud
# runs) -- this stack only references them by name via IAM, same as the CDK
# stack. See infra/jaffle_shop_infra/stack.py for the full rationale.
GLUE_DATABASE_NAME = config.get("glueDatabaseName") or "jaffle_shop"
RAW_DATABASE_NAME = config.get("rawDatabaseName") or "raw"
ATHENA_WORKGROUP = config.get("athenaWorkgroup") or "primary"
DAILY_SCHEDULE_CRON = config.get("dailyScheduleCron") or "cron(0 6 * * ? *)"  # 06:00 UTC daily
# See stack.py's SCHEDULE_STATE comment: this gets re-applied on every `pulumi up`
# that touches the schedule, so toggle it here, not via the console/CLI.
SCHEDULE_STATE = config.get("scheduleState") or "DISABLED"
GEN_YEARS = config.get("genYears") or "1"

current = aws.get_caller_identity()
partition = aws.get_partition()
azs = aws.get_availability_zones(state="available")

# ---------------------------------------------------------------------------
# Network: mirrors CDK's ec2.Vpc(max_azs=2, nat_gateways=0, public-only) --
# two public subnets across two AZs, no NAT Gateway. See INFRA.md's
# "Networking: public subnets, no NAT" for the cost/security trade-off.
# ---------------------------------------------------------------------------

vpc = aws.ec2.Vpc(
    "vpc",
    cidr_block="10.0.0.0/16",
    enable_dns_support=True,
    enable_dns_hostnames=True,
    tags={"Name": f"{NAME_PREFIX}-vpc"},
)

igw = aws.ec2.InternetGateway(
    "igw", vpc_id=vpc.id, tags={"Name": f"{NAME_PREFIX}-igw"}
)

public_subnets = [
    aws.ec2.Subnet(
        f"public-{i}",
        vpc_id=vpc.id,
        cidr_block=f"10.0.{i}.0/24",
        availability_zone=azs.names[i],
        map_public_ip_on_launch=True,
        tags={"Name": f"{NAME_PREFIX}-public-{i}"},
    )
    for i in range(2)
]

public_rt = aws.ec2.RouteTable(
    "public-rt",
    vpc_id=vpc.id,
    routes=[
        aws.ec2.RouteTableRouteArgs(cidr_block="0.0.0.0/0", gateway_id=igw.id)
    ],
    tags={"Name": f"{NAME_PREFIX}-public-rt"},
)

for i, subnet in enumerate(public_subnets):
    aws.ec2.RouteTableAssociation(
        f"public-rta-{i}", subnet_id=subnet.id, route_table_id=public_rt.id
    )

# ---------------------------------------------------------------------------
# S3 data bucket
# ---------------------------------------------------------------------------

# S3 bucket names are globally unique, not just account-unique -- account ID
# + region are baked in alongside the prefix, same reasoning as the CDK
# stack's bucket_name (see infra/jaffle_shop_infra/stack.py).
data_bucket = aws.s3.Bucket(
    "data-bucket",
    bucket=f"{NAME_PREFIX}-data-{current.account_id}-{AWS_REGION}",
    force_destroy=True,  # mirrors CDK's auto_delete_objects=True -- sandbox, no safety net
    tags={"Name": f"{NAME_PREFIX}-data"},
)

aws.s3.BucketServerSideEncryptionConfiguration(
    "data-bucket-sse",
    bucket=data_bucket.id,
    rules=[
        aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256"
            )
        )
    ],
)

aws.s3.BucketPublicAccessBlock(
    "data-bucket-pab",
    bucket=data_bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.s3.BucketPolicy(
    "data-bucket-enforce-ssl",
    bucket=data_bucket.id,
    policy=data_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "EnforceSSL",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [arn, f"{arn}/*"],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            }
        )
    ),
)

# ---------------------------------------------------------------------------
# ECR repo + image build/push
#
# Unlike CDK's ecr_assets.DockerImageAsset (which always builds/pushes into
# a shared, CDK-managed bootstrap repo -- see infra/'s "The Docker image"
# doc section) and unlike Terraform (which needs a third-party provider),
# pulumi_docker_build builds and pushes directly to whatever repo you point
# it at, natively, in one resource -- no copy step, no extra provider.
# ---------------------------------------------------------------------------

dbt_repo = aws.ecr.Repository(
    "dbt-repo",
    name=f"{NAME_PREFIX}-dbt",
    force_delete=True,  # mirrors CDK's empty_on_delete=True -- sandbox, no safety net
)

ecr_auth = aws.ecr.get_authorization_token_output(registry_id=current.account_id)

dbt_image = docker_build.Image(
    "dbt-image",
    context=docker_build.BuildContextArgs(location="../"),
    dockerfile=docker_build.DockerfileArgs(location="../Dockerfile"),
    platforms=[docker_build.Platform.LINUX_ARM64],
    tags=[dbt_repo.repository_url.apply(lambda url: f"{url}:latest")],
    push=True,
    registries=[
        docker_build.RegistryArgs(
            address=dbt_repo.repository_url,
            username=ecr_auth.user_name,
            password=ecr_auth.password,
        )
    ],
)

# ---------------------------------------------------------------------------
# IAM: three roles, deliberately separate and least-privilege -- see
# ROLES.md at the repo root for the full rationale (written against the CDK
# stack, but the same three-role split applies here unchanged).
# ---------------------------------------------------------------------------

ecs_tasks_assume_role_policy = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            }
        ],
    }
)

# ---- task role: what the running dbt process can do ----

task_role = aws.iam.Role(
    "dbt-task-role",
    name=f"{NAME_PREFIX}-dbt-task-role",
    assume_role_policy=ecs_tasks_assume_role_policy,
)

# CDK's data_bucket.grant_read_write(task_role) computes this exact action
# list; Pulumi has no equivalent grant() helper on the base aws.s3.Bucket,
# so it's spelled out by hand here, same as the Terraform version.
task_s3_policy = aws.iam.Policy(
    "dbt-task-s3-access",
    name=f"{NAME_PREFIX}-dbt-task-s3-access",
    policy=data_bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject*", "s3:GetBucket*", "s3:List*",
                            "s3:DeleteObject*", "s3:PutObject", "s3:PutObjectLegalHold",
                            "s3:PutObjectRetention", "s3:PutObjectTagging",
                            "s3:PutObjectVersionTagging", "s3:Abort*",
                        ],
                        "Resource": [arn, f"{arn}/*"],
                    }
                ],
            }
        )
    ),
)

task_glue_policy = aws.iam.Policy(
    "dbt-task-glue-access",
    name=f"{NAME_PREFIX}-dbt-task-glue-access",
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
                        "glue:GetTableVersion", "glue:GetTableVersions", "glue:GetPartition",
                        "glue:GetPartitions", "glue:BatchGetPartition", "glue:CreateTable",
                        "glue:UpdateTable", "glue:DeleteTable", "glue:BatchCreatePartition",
                        "glue:BatchDeletePartition", "glue:BatchDeleteTable",
                    ],
                    "Resource": [
                        f"arn:{partition.partition}:glue:{AWS_REGION}:{current.account_id}:catalog",
                        f"arn:{partition.partition}:glue:{AWS_REGION}:{current.account_id}:database/{GLUE_DATABASE_NAME}",
                        f"arn:{partition.partition}:glue:{AWS_REGION}:{current.account_id}:database/{RAW_DATABASE_NAME}",
                        f"arn:{partition.partition}:glue:{AWS_REGION}:{current.account_id}:table/{GLUE_DATABASE_NAME}/*",
                        f"arn:{partition.partition}:glue:{AWS_REGION}:{current.account_id}:table/{RAW_DATABASE_NAME}/*",
                    ],
                }
            ],
        }
    ),
)

task_athena_policy = aws.iam.Policy(
    "dbt-task-athena-access",
    name=f"{NAME_PREFIX}-dbt-task-athena-access",
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "athena:StartQueryExecution", "athena:GetQueryExecution",
                        "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup",
                    ],
                    "Resource": f"arn:{partition.partition}:athena:{AWS_REGION}:{current.account_id}:workgroup/{ATHENA_WORKGROUP}",
                }
            ],
        }
    ),
)

for i, policy in enumerate([task_s3_policy, task_glue_policy, task_athena_policy]):
    aws.iam.RolePolicyAttachment(
        f"dbt-task-attach-{i}", role=task_role.name, policy_arn=policy.arn
    )

# ---- execution role: what ECS itself needs to start the container ----

task_execution_role = aws.iam.Role(
    "dbt-task-execution-role",
    name=f"{NAME_PREFIX}-dbt-task-execution-role",
    assume_role_policy=ecs_tasks_assume_role_policy,
)

log_group = aws.cloudwatch.LogGroup(
    "dbt-build-log-group",
    name=f"/{NAME_PREFIX}/dbt-build",
    retention_in_days=14,
)

task_execution_policy = aws.iam.Policy(
    "dbt-task-execution-access",
    name=f"{NAME_PREFIX}-dbt-task-execution-access",
    policy=pulumi.Output.all(dbt_repo.arn, log_group.arn).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:GetDownloadUrlForLayer",
                            "ecr:BatchGetImage",
                        ],
                        "Resource": args[0],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "ecr:GetAuthorizationToken",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                        "Resource": args[1],
                    },
                ],
            }
        )
    ),
)

aws.iam.RolePolicyAttachment(
    "dbt-task-execution-attach",
    role=task_execution_role.name,
    policy_arn=task_execution_policy.arn,
)

# ---- scheduler role: what EventBridge Scheduler needs to start the task ----

scheduler_role = aws.iam.Role(
    "scheduler-role",
    name=f"{NAME_PREFIX}-scheduler-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "scheduler.amazonaws.com"},
                    "Condition": {
                        "StringEquals": {"aws:SourceAccount": current.account_id}
                    },
                }
            ],
        }
    ),
)

# ---------------------------------------------------------------------------
# ECS cluster, security group, task definition
# ---------------------------------------------------------------------------

cluster = aws.ecs.Cluster("cluster", name=f"{NAME_PREFIX}-cluster")

task_sg = aws.ec2.SecurityGroup(
    "task-sg",
    name=f"{NAME_PREFIX}-task-sg",
    description="dbt build Fargate task -- outbound only, no inbound",
    vpc_id=vpc.id,
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
        )
    ],
)

# family is set explicitly (not left to Pulumi's auto-generated default)
# so it's identifiable and so scheduler_run_task's IAM policy below can
# reference it directly by ARN.
task_definition = aws.ecs.TaskDefinition(
    "dbt-build-task-def",
    family=f"{NAME_PREFIX}-dbt-build",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu="1024",
    memory="2048",
    execution_role_arn=task_execution_role.arn,
    task_role_arn=task_role.arn,
    runtime_platform=aws.ecs.TaskDefinitionRuntimePlatformArgs(
        cpu_architecture="ARM64", operating_system_family="LINUX"
    ),
    container_definitions=pulumi.Output.all(
        dbt_image.ref, log_group.name, data_bucket.bucket
    ).apply(
        lambda args: json.dumps(
            [
                {
                    "name": "DbtBuild",
                    "image": args[0],
                    "essential": True,
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": args[1],
                            "awslogs-region": AWS_REGION,
                            "awslogs-stream-prefix": "dbt",
                        },
                    },
                    "environment": [
                        {"name": "DBT_TARGET", "value": "prod"},
                        {"name": "AWS_REGION", "value": AWS_REGION},
                        {"name": "DBT_ATHENA_BUCKET", "value": args[2]},
                        {"name": "DBT_ATHENA_SCHEMA", "value": GLUE_DATABASE_NAME},
                        {"name": "DBT_ATHENA_WORKGROUP", "value": ATHENA_WORKGROUP},
                        {"name": "YEARS", "value": GEN_YEARS},
                    ],
                }
            ]
        )
    ),
)

scheduler_run_task_policy = aws.iam.Policy(
    "scheduler-run-task",
    name=f"{NAME_PREFIX}-scheduler-run-task",
    policy=pulumi.Output.all(task_role.arn, task_execution_role.arn).apply(
        lambda args: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "ecs:RunTask",
                        "Resource": f"arn:{partition.partition}:ecs:{AWS_REGION}:{current.account_id}:task-definition/{NAME_PREFIX}-dbt-build:*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iam:PassRole",
                        "Resource": [args[0], args[1]],
                        "Condition": {
                            "StringLike": {
                                "iam:PassedToService": "ecs-tasks.amazonaws.com"
                            }
                        },
                    },
                ],
            }
        )
    ),
)

aws.iam.RolePolicyAttachment(
    "scheduler-run-task-attach",
    role=scheduler_role.name,
    policy_arn=scheduler_run_task_policy.arn,
)

# ---------------------------------------------------------------------------
# EventBridge Scheduler
# ---------------------------------------------------------------------------

daily_schedule = aws.scheduler.Schedule(
    "daily-dbt-build",
    name=f"{NAME_PREFIX}-daily-dbt-build",
    state=SCHEDULE_STATE,
    group_name="default",
    schedule_expression=DAILY_SCHEDULE_CRON,
    schedule_expression_timezone="UTC",
    flexible_time_window=aws.scheduler.ScheduleFlexibleTimeWindowArgs(mode="OFF"),
    target=aws.scheduler.ScheduleTargetArgs(
        arn=cluster.arn,
        role_arn=scheduler_role.arn,
        ecs_parameters=aws.scheduler.ScheduleTargetEcsParametersArgs(
            task_definition_arn=task_definition.arn,
            launch_type="FARGATE",
            task_count=1,
            network_configuration=aws.scheduler.ScheduleTargetEcsParametersNetworkConfigurationArgs(
                subnets=[s.id for s in public_subnets],
                security_groups=[task_sg.id],
                assign_public_ip=True,
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

pulumi.export("dbtRepositoryUri", dbt_repo.repository_url)
pulumi.export("clusterName", cluster.name)
pulumi.export("taskDefinitionFamily", task_definition.family)
pulumi.export("taskDefinitionArn", task_definition.arn)
pulumi.export("logGroupName", log_group.name)
pulumi.export("dataBucketName", data_bucket.bucket)
pulumi.export("glueDatabaseName", GLUE_DATABASE_NAME)
pulumi.export("rawDatabaseName", RAW_DATABASE_NAME)
pulumi.export("athenaWorkgroup", ATHENA_WORKGROUP)
pulumi.export("taskSecurityGroupId", task_sg.id)
pulumi.export("publicSubnetIds", [s.id for s in public_subnets])
pulumi.export("taskRoleArn", task_role.arn)
pulumi.export("taskExecutionRoleArn", task_execution_role.arn)
pulumi.export("schedulerRoleArn", scheduler_role.arn)
