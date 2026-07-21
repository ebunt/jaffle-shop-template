from pathlib import Path

from aws_cdk import (
    Aws,
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_scheduler as scheduler,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
GLUE_DATABASE_NAME = "jaffle_shop"
# dbt/macros/generate_schema_name.sql hardcodes seeds to a separate "raw"
# schema regardless of target, so seeds land in their own Glue Database.
RAW_DATABASE_NAME = "raw"
ATHENA_WORKGROUP = "primary"
DAILY_SCHEDULE_CRON = "cron(0 6 * * ? *)"  # 06:00 UTC daily


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

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        log_group = logs.LogGroup(
            self,
            "TaskLogGroup",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        task_role = iam.Role(
            self,
            "DbtTaskRole",
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
        task_role.add_to_policy(
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
        )
        workgroup_arn = (
            f"arn:{Aws.PARTITION}:athena:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
            f"workgroup/{ATHENA_WORKGROUP}"
        )
        task_role.add_to_policy(
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
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "DbtBuildTaskDef",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=task_role,  # ty: ignore[invalid-argument-type]
        )
        task_definition.add_container(
            "DbtBuild",
            image=ecs.ContainerImage.from_docker_image_asset(image_asset),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="dbt", log_group=log_group),
            environment={
                "DBT_TARGET": "prod",
                "AWS_REGION": Aws.REGION,
                "DBT_ATHENA_BUCKET": data_bucket.bucket_name,
                "DBT_ATHENA_SCHEMA": GLUE_DATABASE_NAME,
                "DBT_ATHENA_WORKGROUP": ATHENA_WORKGROUP,
            },
        )

        task_security_group = ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=vpc,
            description="dbt build Fargate task -- outbound only, no inbound",
            allow_all_outbound=True,
        )

        scheduler_role = iam.Role(
            self,
            "SchedulerExecutionRole",
            assumed_by=iam.ServicePrincipal(  # ty: ignore[invalid-argument-type]
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID}},
            ),
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[
                    f"arn:{Aws.PARTITION}:ecs:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
                    f"task-definition/{task_definition.family}:*"
                ],
            )
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[
                    task_role.role_arn,
                    task_definition.obtain_execution_role().role_arn,
                ],
                conditions={
                    "StringLike": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}
                },
            )
        )

        public_subnet_ids = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PUBLIC
        ).subnet_ids

        scheduler.CfnSchedule(
            self,
            "DailyDbtBuildSchedule",
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
