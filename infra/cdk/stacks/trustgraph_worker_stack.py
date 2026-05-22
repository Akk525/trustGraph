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
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
)
from constructs import Construct

#: ECR images — update tags when new images are pushed.
WORKER_IMAGE_URI = (
    "742543724243.dkr.ecr.us-east-1.amazonaws.com/trustgraph-worker:latest"
)
API_IMAGE_URI = (
    "742543724243.dkr.ecr.us-east-1.amazonaws.com/trustgraph-api:latest"
)

QUEUE_NAME = "trustgraph-jobs"
DLQ_NAME = "trustgraph-jobs-dlq"
LOG_GROUP_NAME = "/ecs/trustgraph-worker"
API_LOG_GROUP_NAME = "/ecs/trustgraph-api"
CLUSTER_NAME = "trustgraph"
SERVICE_NAME = "trustgraph-worker"
API_SERVICE_NAME = "trustgraph-api"
DYNAMODB_TABLE_NAME = "trustgraph-jobs"
USERS_TABLE_NAME = "trustgraph-users"
API_KEYS_TABLE_NAME = "trustgraph-api-keys"

# GSI names — referenced by application code and tests.
JOBS_USER_INDEX = "user_id-created_at-index"
JOBS_STATUS_INDEX = "status-created_at-index"
USERS_EMAIL_INDEX = "email-index"
API_KEYS_HASH_INDEX = "key_hash-index"
API_KEYS_USER_INDEX = "user_id-created_at-index"
S3_PREFIX = "trustgraph/jobs"
INPUT_S3_PREFIX = "trustgraph/inputs"
JWT_SECRET_NAME = "trustgraph/jwt-secret"


class TrustGraphWorkerStack(Stack):
    """
    TrustGraph platform stack — Phase 4A.

    Provisions all shared infrastructure plus both ECS services:

    Shared:
        SQS (jobs queue + DLQ), S3 bucket, DynamoDB (jobs + auth tables),
        ECS cluster, CloudWatch log groups, JWT secret in Secrets Manager.

    Worker (Phase 2C, unchanged):
        Fargate task + service — polls SQS, executes audits, writes artifacts.

    API (Phase 2D):
        Fargate task + service — serves FastAPI behind an Application Load
        Balancer; no embedded worker.

    Cost controls:
    - No NAT Gateway: all tasks use public subnets with a public IP.
    - CPU/memory sized for expected load (worker 512/1024, API 256/512).
    - Desired counts configurable via CDK context.
    - S3 and auth table removal policies set for dev/staging.

    Deploy:
        cd infra/cdk
        cdk deploy
        cdk deploy --context desired_count=0 --context api_desired_count=0
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        desired_count = int(self.node.try_get_context("desired_count") or 1)
        api_desired_count = int(self.node.try_get_context("api_desired_count") or 1)
        # Phase 5C: comma-separated frontend origins, e.g.
        #   cdk deploy --context cors_origins=https://myapp.vercel.app
        cors_origins: str = self.node.try_get_context("cors_origins") or ""

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
                ),
                s3.LifecycleRule(
                    # Uploaded ZIPs are transient: worker downloads once.
                    id="ExpireInputUploads",
                    prefix=f"{INPUT_S3_PREFIX}/",
                    expiration=Duration.days(1),
                ),
            ],
        )

        # ------------------------------------------------------------------
        # DynamoDB — job store (shared by API + worker)
        # Phase 4A GSIs:
        #   user_id-created_at-index  — list/page a user's jobs (GET /audits, quotas)
        #   status-created_at-index   — list all jobs of a given status (ops/admin)
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
        jobs_table.add_global_secondary_index(
            index_name=JOBS_USER_INDEX,
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        jobs_table.add_global_secondary_index(
            index_name=JOBS_STATUS_INDEX,
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ------------------------------------------------------------------
        # DynamoDB — auth tables
        # Phase 4A GSIs:
        #   users: email-index — O(log n) email lookup at login/signup
        #   api_keys: key_hash-index — O(log n) auth lookup on every request
        #             user_id-created_at-index — list keys for a user
        # ------------------------------------------------------------------
        users_table = dynamodb.Table(
            self,
            "UsersTable",
            table_name=USERS_TABLE_NAME,
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        users_table.add_global_secondary_index(
            index_name=USERS_EMAIL_INDEX,
            partition_key=dynamodb.Attribute(
                name="email",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        api_keys_table = dynamodb.Table(
            self,
            "ApiKeysTable",
            table_name=API_KEYS_TABLE_NAME,
            partition_key=dynamodb.Attribute(
                name="key_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        api_keys_table.add_global_secondary_index(
            index_name=API_KEYS_HASH_INDEX,
            partition_key=dynamodb.Attribute(
                name="key_hash",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        api_keys_table.add_global_secondary_index(
            index_name=API_KEYS_USER_INDEX,
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ------------------------------------------------------------------
        # Secrets Manager — JWT signing key
        # Generated at deploy time.  To rotate: delete the secret and redeploy
        # (existing JWTs are invalidated — all sessions end).
        # ------------------------------------------------------------------
        jwt_secret = secretsmanager.Secret(
            self,
            "ApiJwtSecret",
            secret_name=JWT_SECRET_NAME,
            description="TRUSTGRAPH_JWT_SECRET — HS256 signing key for API JWTs",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=64,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------
        # CloudWatch — log groups (worker + API)
        # ------------------------------------------------------------------
        log_group = logs.LogGroup(
            self,
            "WorkerLogGroup",
            log_group_name=LOG_GROUP_NAME,
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        api_log_group = logs.LogGroup(
            self,
            "ApiLogGroup",
            log_group_name=API_LOG_GROUP_NAME,
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------
        # ECS cluster (shared by worker + API)
        # ------------------------------------------------------------------
        cluster = ecs.Cluster(
            self,
            "WorkerCluster",
            cluster_name=CLUSTER_NAME,
            vpc=vpc,
        )

        # ==================================================================
        # WORKER — IAM roles, task definition, service  (Phase 2C, unchanged)
        # ==================================================================

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

        task_role = iam.Role(
            self,
            "TaskRole",
            role_name="trustgraph-worker-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        task_role.add_to_policy(iam.PolicyStatement(
            sid="SQSWorker",
            effect=iam.Effect.ALLOW,
            actions=[
                "sqs:ReceiveMessage",
                "sqs:DeleteMessage",
                "sqs:GetQueueAttributes",
                "sqs:ChangeMessageVisibility",
            ],
            resources=[queue.queue_arn],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            sid="S3ArtifactObjects",
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject", "s3:GetObject"],
            resources=[f"{bucket.bucket_arn}/{S3_PREFIX}/*"],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            sid="S3ArtifactList",
            effect=iam.Effect.ALLOW,
            actions=["s3:ListBucket"],
            resources=[bucket.bucket_arn],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            sid="S3InputGet",
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject"],
            resources=[f"{bucket.bucket_arn}/{INPUT_S3_PREFIX}/*"],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            sid="DynamoDBJobStore",
            effect=iam.Effect.ALLOW,
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan", "dynamodb:Query"],
            resources=[
                jobs_table.table_arn,
                f"{jobs_table.table_arn}/index/*",
            ],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchLogs",
            effect=iam.Effect.ALLOW,
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"{log_group.log_group_arn}:*"],
        ))

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
                "TRUSTGRAPH_DEMO_SOURCE_PATH": "/build/examples/vulnerable-crosschain/src",
                "TRUSTGRAPH_INPUT_S3_PREFIX": INPUT_S3_PREFIX,
                "AWS_REGION": self.region,
            },
        )

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

        # ==================================================================
        # API — IAM roles, task definition, ALB, service  (Phase 2D)
        # ==================================================================

        # Execution role — needs Secrets Manager access to inject JWT secret
        # at container startup (ECS reads it, not the app).
        api_execution_role = iam.Role(
            self,
            "ApiTaskExecutionRole",
            role_name="trustgraph-api-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        # Grant execution role access to fetch the JWT secret at startup.
        jwt_secret.grant_read(api_execution_role)

        # Task role — least-privilege application permissions.
        api_task_role = iam.Role(
            self,
            "ApiTaskRole",
            role_name="trustgraph-api-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # DynamoDB — jobs table (create, read, list user's jobs via GSI)
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="DynamoDBJobsTable",
            effect=iam.Effect.ALLOW,
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan", "dynamodb:Query"],
            resources=[
                jobs_table.table_arn,
                f"{jobs_table.table_arn}/index/*",
            ],
        ))
        # DynamoDB — user accounts (signup, email lookup via GSI)
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="DynamoDBUsersTable",
            effect=iam.Effect.ALLOW,
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"],
            resources=[
                users_table.table_arn,
                f"{users_table.table_arn}/index/*",
            ],
        ))
        # DynamoDB — API keys (create, revoke, hash lookup via GSI)
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="DynamoDBApiKeysTable",
            effect=iam.Effect.ALLOW,
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"],
            resources=[
                api_keys_table.table_arn,
                f"{api_keys_table.table_arn}/index/*",
            ],
        ))
        # SQS — enqueue jobs (SendMessage) + health check (GetQueueAttributes)
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="SQSSendMessage",
            effect=iam.Effect.ALLOW,
            actions=["sqs:SendMessage", "sqs:GetQueueAttributes"],
            resources=[queue.queue_arn],
        ))
        # S3 — sign presigned PUT URLs so clients can upload ZIPs directly
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="S3InputUpload",
            effect=iam.Effect.ALLOW,
            actions=["s3:PutObject"],
            resources=[f"{bucket.bucket_arn}/{INPUT_S3_PREFIX}/*"],
        ))
        # S3 — sign presigned GET URLs for artifact downloads
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="S3ArtifactRead",
            effect=iam.Effect.ALLOW,
            actions=["s3:GetObject"],
            resources=[f"{bucket.bucket_arn}/{S3_PREFIX}/*"],
        ))
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="S3ArtifactListBucket",
            effect=iam.Effect.ALLOW,
            actions=["s3:ListBucket"],
            resources=[bucket.bucket_arn],
        ))
        api_task_role.add_to_policy(iam.PolicyStatement(
            sid="CloudWatchApiLogs",
            effect=iam.Effect.ALLOW,
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"{api_log_group.log_group_arn}:*"],
        ))

        api_task_def = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDef",
            family="trustgraph-api",
            cpu=256,
            memory_limit_mib=512,
            task_role=api_task_role,
            execution_role=api_execution_role,
        )

        api_task_def.add_container(
            "api",
            image=ecs.ContainerImage.from_registry(API_IMAGE_URI),
            logging=ecs.LogDrivers.aws_logs(
                log_group=api_log_group,
                stream_prefix="api",
            ),
            environment={
                "TRUSTGRAPH_EMBEDDED_WORKER": "false",
                "TRUSTGRAPH_AUTH_REQUIRED": "true",
                "TRUSTGRAPH_AUTH_STORE": "dynamodb",
                "TRUSTGRAPH_JOB_STORE": "dynamodb",
                "TRUSTGRAPH_JOB_QUEUE": "sqs",
                "TRUSTGRAPH_ARTIFACT_STORE": "s3",
                "TRUSTGRAPH_SQS_QUEUE_URL": queue.queue_url,
                "TRUSTGRAPH_S3_BUCKET": bucket.bucket_name,
                "TRUSTGRAPH_S3_PREFIX": S3_PREFIX,
                "TRUSTGRAPH_INPUT_S3_PREFIX": INPUT_S3_PREFIX,
                "TRUSTGRAPH_DYNAMODB_TABLE": jobs_table.table_name,
                "TRUSTGRAPH_USERS_TABLE": users_table.table_name,
                "TRUSTGRAPH_API_KEYS_TABLE": api_keys_table.table_name,
                "TRUSTGRAPH_LOG_LEVEL": "INFO",
                "TRUSTGRAPH_CORS_ORIGINS": cors_origins,
                "AWS_REGION": self.region,
            },
            # JWT secret is injected at container startup by ECS from
            # Secrets Manager — never stored in the task definition plaintext.
            secrets={
                "TRUSTGRAPH_JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_secret),
            },
            port_mappings=[ecs.PortMapping(container_port=8000)],
        )

        # Application Load Balancer — public, port 80 → API tasks port 8000
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "ApiAlb",
            vpc=vpc,
            internet_facing=True,
            load_balancer_name="trustgraph-api",
        )

        listener = alb.add_listener(
            "HttpListener",
            port=80,
            open=True,
        )

        api_service = ecs.FargateService(
            self,
            "ApiService",
            service_name=API_SERVICE_NAME,
            cluster=cluster,
            task_definition=api_task_def,
            desired_count=api_desired_count,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
        )

        listener.add_targets(
            "ApiTargets",
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[api_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                timeout=Duration.seconds(10),
            ),
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
        CfnOutput(self, "ServiceName", value=SERVICE_NAME)           # backward compat
        CfnOutput(self, "WorkerServiceName", value=SERVICE_NAME)
        CfnOutput(self, "ApiServiceName", value=API_SERVICE_NAME)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        CfnOutput(self, "ApiLogGroupName", value=api_log_group.log_group_name)
        CfnOutput(self, "WorkerImageUri", value=WORKER_IMAGE_URI)
        CfnOutput(self, "ApiImageUri", value=API_IMAGE_URI)
        CfnOutput(self, "ApiUrl", value=f"http://{alb.load_balancer_dns_name}")
