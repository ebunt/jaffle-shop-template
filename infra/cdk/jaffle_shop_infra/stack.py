from pathlib import Path

import cdk_ecr_deployment as ecrdeploy
from aws_cdk import (
    Aws,
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    # aws_glue as glue,  # uncomment along with the CfnDatabase blocks below
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_scheduler as scheduler,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
# Explicit names below are prefixed with this so every jaffle-shop-owned IAM/ECS/etc.
# artifact is identifiable in the account, instead of CDK's auto-generated
# <stack>-<construct-path>-<hash> physical names. This only works because this
# stack is deployed once per account (see infra/app.py) -- IAM role/policy and
# S3 bucket names must be unique account-wide (S3: globally unique), so a
# second deploy of this stack into the same account would collide on these.
NAME_PREFIX = "jaffle-shop"
GLUE_DATABASE_NAME = "jaffle_shop"
# dbt/macros/generate_schema_name.sql hardcodes seeds to a separate "raw"
# schema regardless of target, so seeds land in their own Glue Database.
RAW_DATABASE_NAME = "raw"
ATHENA_WORKGROUP = "primary"
DAILY_SCHEDULE_CRON = "cron(0 6 * * ? *)"  # 06:00 UTC daily
# "ENABLED" | "DISABLED". Toggling this via the AWS CLI/console instead of
# here doesn't stick: CloudFormation applies State's default ("ENABLED")
# any time it has to update this resource for an unrelated reason -- e.g.
# every task definition change, including image/env var updates, since
# the schedule's Target embeds the task definition's ARN (which changes
# per revision). Change it here and redeploy instead.
SCHEDULE_STATE = "DISABLED"
# The task is sized at 1024 CPU / 2048 MiB; `make gen`'s default YEARS=6
# OOM-killed it (jafgen holds all the synthetic data in memory before
# writing it out). YEARS=1 is what every live validation run since has
# actually used and confirmed works -- bump this (and the task size) if
# you want more historical data instead.
GEN_YEARS = "1"


class JaffleShopStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )

        # Sandbox project: destroy cleanly on `cdk destroy` rather than
        # leaving orphaned data behind.
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            # S3 bucket names are unique *globally* (not just per-account), so
            # account ID + region are baked in alongside the human-readable
            # prefix to guarantee that regardless of what other AWS accounts
            # exist.
            bucket_name=f"{NAME_PREFIX}-data-{Aws.ACCOUNT_ID}-{Aws.REGION}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Both Glue Databases (jaffle_shop, raw) are pre-existing -- from
        # prior dbt Cloud runs against this account -- so this stack only
        # references them by name via IAM, and never creates or (on
        # `cdk destroy`) deletes them.
        #
        # If you're deploying this template into a *fresh* account (neither
        # database exists yet), you'll need to create them yourself first,
        # or add glue.CfnDatabase resources back in.
        #
        # If you'd previously deployed an older commit of this stack that
        # DID include glue.CfnDatabase resources for these two databases
        # (and that deploy succeeded, so CloudFormation now owns them):
        # deploying this version as-is will DELETE both databases and their
        # table metadata, because removing a resource from the template
        # defaults to DeletionPolicy=Delete. Deploy once with
        # `removal_policy=RemovalPolicy.RETAIN` set on those constructs
        # first, then remove them, so CloudFormation orphans rather than
        # deletes them.
        #
        # Uncomment this (and the `aws_glue as glue` import above) to have
        # CDK create the databases instead, e.g. when deploying into a
        # fresh account where they don't already exist:
        #
        # glue_database = glue.CfnDatabase(
        #     self,
        #     "GlueDatabase",
        #     catalog_id=Aws.ACCOUNT_ID,
        #     database_input=glue.CfnDatabase.DatabaseInputProperty(
        #         name=GLUE_DATABASE_NAME
        #     ),
        # )
        # raw_glue_database = glue.CfnDatabase(
        #     self,
        #     "RawGlueDatabase",
        #     catalog_id=Aws.ACCOUNT_ID,
        #     database_input=glue.CfnDatabase.DatabaseInputProperty(
        #         name=RAW_DATABASE_NAME
        #     ),
        # )
        #
        # ...and add a dependency once `task_definition` exists (see the
        # commented lines right after `task_definition.add_container(...)`
        # below) so the task waits for them to exist.

        # Build for linux/arm64 explicitly: paired with runtime_platform=ARM64
        # below, this runs the task on Graviton (cheaper than X86_64) and
        # matches this repo's Apple Silicon dev machines for native
        # (non-emulated) local `docker build`. The base image
        # (ghcr.io/astral-sh/uv) publishes both platforms.
        image_asset = ecr_assets.DockerImageAsset(
            self,
            "DbtImage",
            directory=str(REPO_ROOT),
            file="Dockerfile",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        # DockerImageAsset always builds/pushes into CDK's shared,
        # content-hash-tagged bootstrap asset repo (no repository_name option
        # exists on it) -- copy it into our own named repo so the image is
        # identifiable in the account instead of living only in the shared
        # cdk-hnb659fds-container-assets-* repo. Tag with the asset's own
        # hash (not "latest") so each code change produces a distinct,
        # immutable tag here too, same as the source asset.
        dbt_repo = ecr.Repository(
            self,
            "DbtRepo",
            repository_name=f"{NAME_PREFIX}-dbt",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )
        dbt_image_deployment = ecrdeploy.ECRDeployment(
            self,
            "DbtImageDeployment",
            src=ecrdeploy.DockerImageName(image_asset.image_uri),
            dest=ecrdeploy.DockerImageName(
                f"{dbt_repo.repository_uri}:{image_asset.asset_hash}"
            ),
        )

        cluster = ecs.Cluster(
            self, "Cluster", cluster_name=f"{NAME_PREFIX}-cluster", vpc=vpc
        )

        log_group = logs.LogGroup(
            self,
            "TaskLogGroup",
            log_group_name=f"/{NAME_PREFIX}/dbt-build",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        task_role = iam.Role(
            self,
            "DbtTaskRole",
            role_name=f"{NAME_PREFIX}-dbt-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),  # ty: ignore[invalid-argument-type]
        )
        data_bucket.grant_read_write(task_role)

        catalog_arn = f"arn:{Aws.PARTITION}:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}:catalog"
        glue_database_names = [GLUE_DATABASE_NAME, RAW_DATABASE_NAME]
        database_arns = [
            f"arn:{Aws.PARTITION}:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}:database/{name}"
            for name in glue_database_names
        ]
        table_arns = [
            f"arn:{Aws.PARTITION}:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}:table/{name}/*"
            for name in glue_database_names
        ]
        iam.ManagedPolicy(
            self,
            "DbtTaskGlueAccessPolicy",
            managed_policy_name=f"{NAME_PREFIX}-dbt-task-glue-access",
            roles=[task_role],  # ty: ignore[invalid-argument-type]
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "glue:GetDatabase",
                        "glue:GetDatabases",
                        "glue:GetTable",
                        "glue:GetTables",
                        "glue:GetTableVersion",
                        "glue:GetTableVersions",
                        "glue:GetPartition",
                        "glue:GetPartitions",
                        "glue:BatchGetPartition",
                        "glue:CreateTable",
                        "glue:UpdateTable",
                        "glue:DeleteTable",
                        "glue:BatchCreatePartition",
                        "glue:BatchDeletePartition",
                        "glue:BatchDeleteTable",
                    ],
                    resources=[catalog_arn, *database_arns, *table_arns],
                )
            ],
        )
        workgroup_arn = (
            f"arn:{Aws.PARTITION}:athena:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
            f"workgroup/{ATHENA_WORKGROUP}"
        )
        iam.ManagedPolicy(
            self,
            "DbtTaskAthenaAccessPolicy",
            managed_policy_name=f"{NAME_PREFIX}-dbt-task-athena-access",
            roles=[task_role],  # ty: ignore[invalid-argument-type]
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "athena:StartQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:StopQueryExecution",
                        "athena:GetWorkGroup",
                    ],
                    resources=[workgroup_arn],
                )
            ],
        )

        # Named in place of CDK's implicit auto-created execution role -- CDK
        # still grants it ECR pull / log write permissions automatically
        # (via the container image asset and log driver below), same as it
        # would for an auto-created one.
        task_execution_role = iam.Role(
            self,
            "DbtTaskExecutionRole",
            role_name=f"{NAME_PREFIX}-dbt-task-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),  # ty: ignore[invalid-argument-type]
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "DbtBuildTaskDef",
            family=f"{NAME_PREFIX}-dbt-build",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            execution_role=task_execution_role,  # ty: ignore[invalid-argument-type]
            task_role=task_role,  # ty: ignore[invalid-argument-type]
        )
        task_definition.add_container(
            "DbtBuild",
            image=ecs.ContainerImage.from_ecr_repository(
                dbt_repo, image_asset.asset_hash
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="dbt", log_group=log_group),
            environment={
                "DBT_TARGET": "prod",
                "AWS_REGION": Aws.REGION,
                "DBT_ATHENA_BUCKET": data_bucket.bucket_name,
                "DBT_ATHENA_SCHEMA": GLUE_DATABASE_NAME,
                "DBT_ATHENA_WORKGROUP": ATHENA_WORKGROUP,
                "YEARS": GEN_YEARS,
            },
        )
        # The container's image tag isn't resolved from the copy's output --
        # it's a static string CDK can't infer a dependency from -- so state
        # the ordering explicitly: the tag must exist in dbt_repo before
        # anything tries to register/run this task definition.
        task_definition.node.add_dependency(dbt_image_deployment)
        # If you uncommented the glue.CfnDatabase blocks above, uncomment
        # these too so the task waits for them to exist:
        # task_definition.node.add_dependency(glue_database)
        # task_definition.node.add_dependency(raw_glue_database)

        task_security_group = ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            security_group_name=f"{NAME_PREFIX}-task-sg",
            vpc=vpc,
            description="dbt build Fargate task -- outbound only, no inbound",
            allow_all_outbound=True,
        )

        scheduler_role = iam.Role(
            self,
            "SchedulerExecutionRole",
            role_name=f"{NAME_PREFIX}-scheduler-role",
            assumed_by=iam.ServicePrincipal(  # ty: ignore[invalid-argument-type]
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID}},
            ),
        )
        iam.ManagedPolicy(
            self,
            "SchedulerRunTaskPolicy",
            managed_policy_name=f"{NAME_PREFIX}-scheduler-run-task",
            roles=[scheduler_role],  # ty: ignore[invalid-argument-type]
            statements=[
                iam.PolicyStatement(
                    actions=["ecs:RunTask"],
                    resources=[
                        f"arn:{Aws.PARTITION}:ecs:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
                        f"task-definition/{task_definition.family}:*"
                    ],
                ),
                iam.PolicyStatement(
                    actions=["iam:PassRole"],
                    resources=[
                        task_role.role_arn,
                        task_execution_role.role_arn,
                    ],
                    conditions={
                        "StringLike": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}
                    },
                ),
            ],
        )

        public_subnet_ids = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PUBLIC
        ).subnet_ids

        scheduler.CfnSchedule(
            self,
            "DailyDbtBuildSchedule",
            name=f"{NAME_PREFIX}-daily-dbt-build",
            state=SCHEDULE_STATE,
            schedule_expression=DAILY_SCHEDULE_CRON,
            schedule_expression_timezone="UTC",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=cluster.cluster_arn,
                role_arn=scheduler_role.role_arn,
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task_definition.task_definition_arn,
                    launch_type="FARGATE",
                    task_count=1,
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=public_subnet_ids,
                            security_groups=[task_security_group.security_group_id],
                            assign_public_ip="ENABLED",
                        )
                    ),
                ),
            ),
        )

        CfnOutput(self, "DbtRepositoryUri", value=dbt_repo.repository_uri)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "TaskDefinitionFamily", value=task_definition.family)
        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "GlueDatabaseName", value=GLUE_DATABASE_NAME)
        CfnOutput(self, "RawGlueDatabaseName", value=RAW_DATABASE_NAME)
        CfnOutput(self, "AthenaWorkgroup", value=ATHENA_WORKGROUP)
        CfnOutput(
            self, "TaskSecurityGroupId", value=task_security_group.security_group_id
        )
        CfnOutput(self, "PublicSubnetIds", value=",".join(public_subnet_ids))
        CfnOutput(self, "TaskRoleArn", value=task_role.role_arn)
        CfnOutput(self, "TaskExecutionRoleArn", value=task_execution_role.role_arn)
        CfnOutput(self, "SchedulerRoleArn", value=scheduler_role.role_arn)
