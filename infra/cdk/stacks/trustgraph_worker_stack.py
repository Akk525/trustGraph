from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_sqs as sqs,
)
from constructs import Construct

#: ECR image built and pushed in Phase 1.5 / pre-Phase 2C.
#: Update this tag when a new worker image is pushed.
WORKER_IMAGE_URI = (
    "742543724243.dkr.ecr.us-east-1.amazonaws.com/trustgraph-worker:latest"
)

QUEUE_NAME = "trustgraph-jobs"
DLQ_NAME = "trustgraph-jobs-dlq"
LOG_GROUP_NAME = "/ecs/trustgraph-worker"
CLUSTER_NAME = "trustgraph"
SERVICE_NAME = "trustgraph-worker"
DYNAMODB_TABLE_NAME = "trustgraph-jobs"
S3_PREFIX = "trustgraph/jobs"


class TrustGraphWorkerStack(Stack):
    """
    Phase 2C ECS Fargate worker stack.

    Provisions everything the worker needs to run independently of the API:
    SQS queue, S3 artifact bucket, DynamoDB job store, ECS cluster, Fargate
    task definition + service, CloudWatch log group, and the IAM roles that
    wire them together.

    Cost controls:
    - CPU 512 / Memory 1024 MiB (smallest Fargate tier that fits the scanner)
    - desired_count configurable via CDK context (--context desired_count=0)
    - No NAT Gateway: tasks run in public subnets with a public IP
    - Log retention: 7 days
    - S3 lifecycle: 14-day expiration on the jobs prefix
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        desired_count = int(self.node.try_get_context("desired_count") or 1)

        # ------------------------------------------------------------------
        # Networking — public-only VPC (no NAT gateway cost)
        # ------------------------------------------------------------------
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        # ------------------------------------------------------------------
        # SQS — dead-letter queue + main queue
        # ------------------------------------------------------------------
        dlq = sqs.Queue(
            self,
            "WorkerDLQ",
            queue_name=DLQ_NAME,
            retention_period=Duration.days(14),
        )

        queue = sqs.Queue(
            self,
            "WorkerQueue",
            queue_name=QUEUE_NAME,
            # Visibility timeout must exceed max expected audit duration.
            # 300 s = 5 min; override with CDK context if audits run longer.
            visibility_timeout=Duration.seconds(300),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=dlq,
            ),
        )

        # ------------------------------------------------------------------
        # S3 — artifact bucket (private, lifecycle expiration)
        # ------------------------------------------------------------------
        bucket_name = f"trustgraph-artifacts-{self.account}"
        bucket = s3.Bucket(
            self,
            "ArtifactBucket",
            bucket_name=bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireJobArtifacts",
                    prefix=f"{S3_PREFIX}/",
                    expiration=Duration.days(14),
                )
            ],
        )

        # ------------------------------------------------------------------
        # DynamoDB — shared job store (API + worker)
        # ------------------------------------------------------------------
        jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name=DYNAMODB_TABLE_NAME,
            partition_key=dynamodb.Attribute(
                name="job_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ------------------------------------------------------------------
        # CloudWatch — structured log group
        # ------------------------------------------------------------------
        log_group = logs.LogGroup(
            self,
            "WorkerLogGroup",
            log_group_name=LOG_GROUP_NAME,
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------
        # ECS cluster
        # ------------------------------------------------------------------
        cluster = ecs.Cluster(
            self,
            "WorkerCluster",
            cluster_name=CLUSTER_NAME,
            vpc=vpc,
        )

        # ------------------------------------------------------------------
        # IAM — execution role (ECR pull + CloudWatch log delivery)
        # ------------------------------------------------------------------
        execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name="trustgraph-worker-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        # ------------------------------------------------------------------
        # IAM — task role (least-privilege SQS + S3 + DynamoDB + logs)
        # ------------------------------------------------------------------
        task_role = iam.Role(
            self,
            "TaskRole",
            role_name="trustgraph-worker-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="SQSWorker",
                effect=iam.Effect.ALLOW,
                actions=[
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                    "sqs:ChangeMessageVisibility",
                ],
                resources=[queue.queue_arn],
            )
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3ArtifactObjects",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:GetObject",
                ],
                resources=[f"{bucket.bucket_arn}/{S3_PREFIX}/*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3ArtifactList",
                effect=iam.Effect.ALLOW,
                actions=["s3:ListBucket"],
                resources=[bucket.bucket_arn],
            )
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBJobStore",
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:Scan",
                ],
                resources=[jobs_table.table_arn],
            )
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogs",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[f"{log_group.log_group_arn}:*"],
            )
        )

        # ------------------------------------------------------------------
        # Fargate task definition
        # ------------------------------------------------------------------
        task_def = ecs.FargateTaskDefinition(
            self,
            "WorkerTaskDef",
            family="trustgraph-worker",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            execution_role=execution_role,
        )

        task_def.add_container(
            "worker",
            # Override Dockerfile ENTRYPOINT to launch the worker-only entrypoint.
            entry_point=["trustgraph-worker"],
            image=ecs.ContainerImage.from_registry(WORKER_IMAGE_URI),
            logging=ecs.LogDrivers.aws_logs(
                log_group=log_group,
                stream_prefix="worker",
            ),
            environment={
                "TRUSTGRAPH_JOB_QUEUE": "sqs",
                "TRUSTGRAPH_ARTIFACT_STORE": "s3",
                "TRUSTGRAPH_EXECUTION_MODE": "local_host",
                "TRUSTGRAPH_SQS_QUEUE_URL": queue.queue_url,
                "TRUSTGRAPH_S3_BUCKET": bucket.bucket_name,
                "TRUSTGRAPH_S3_PREFIX": S3_PREFIX,
                "TRUSTGRAPH_JOB_STORE": "dynamodb",
                "TRUSTGRAPH_DYNAMODB_TABLE": jobs_table.table_name,
                "TRUSTGRAPH_WORKER_ONLY": "true",
                "TRUSTGRAPH_LOG_LEVEL": "INFO",
                # Dockerfile.worker copies examples/ into /build/ — point the
                # audit service at the correct container path explicitly so it
                # does not fall back to the site-packages relative path.
                "TRUSTGRAPH_DEMO_SOURCE_PATH": "/build/examples/vulnerable-crosschain/src",
                "AWS_REGION": self.region,
            },
        )

        # ------------------------------------------------------------------
        # Fargate service
        # ------------------------------------------------------------------
        ecs.FargateService(
            self,
            "WorkerService",
            service_name=SERVICE_NAME,
            cluster=cluster,
            task_definition=task_def,
            desired_count=desired_count,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
        )

        # ------------------------------------------------------------------
        # Stack outputs
        # ------------------------------------------------------------------
        CfnOutput(self, "QueueUrl", value=queue.queue_url)
        CfnOutput(self, "QueueArn", value=queue.queue_arn)
        CfnOutput(self, "DLQUrl", value=dlq.queue_url)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "JobsTableName", value=jobs_table.table_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=SERVICE_NAME)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        CfnOutput(self, "WorkerImageUri", value=WORKER_IMAGE_URI)
