"""
Phase 2C tests — DynamoDBJobStore (moto), worker_main config validation, CDK synthesis.

All AWS calls use moto mocks. CDK tests require aws-cdk-lib to be installed;
they are skipped when the package is absent (it lives in infra/cdk/requirements.txt,
not in the project dev deps).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ── optional deps ──────────────────────────────────────────────────────────────
try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

try:
    import aws_cdk as cdk
    from aws_cdk import assertions as cdk_assertions
    # The stack lives outside the normal Python path; add it temporarily.
    _CDK_DIR = Path(__file__).parent.parent.parent / "infra" / "cdk"
    sys.path.insert(0, str(_CDK_DIR))
    from stacks.trustgraph_worker_stack import TrustGraphWorkerStack, WORKER_IMAGE_URI
    HAS_CDK = True
except ImportError:
    HAS_CDK = False

from trustgraph_cloud.jobs.models import Job, JobStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

REGION = "us-east-1"
TABLE_NAME = "test-trustgraph-jobs"


def _create_table(client):
    """Create the jobs table with Phase 4A GSIs (matches production schema)."""
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "job_id",     "AttributeType": "S"},
            {"AttributeName": "user_id",    "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
            {"AttributeName": "status",     "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "user_id-created_at-index",
                "KeySchema": [
                    {"AttributeName": "user_id",    "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "status-created_at-index",
                "KeySchema": [
                    {"AttributeName": "status",     "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


# ─────────────────────────────────────────────────────────────────────────────
# DynamoDBJobStore
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBJobStoreCreate(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=TABLE_NAME, region=REGION)

    def tearDown(self):
        self.mock.stop()

    def test_create_returns_job(self):
        job = Job(input_type="demo")
        result = self.store.create(job)
        self.assertEqual(result.job_id, job.job_id)

    def test_create_persists_to_dynamo(self):
        job = Job(input_type="demo")
        self.store.create(job)
        fetched = self.store.get(job.job_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.job_id, job.job_id)

    def test_get_nonexistent_returns_none(self):
        result = self.store.get("does-not-exist")
        self.assertIsNone(result)

    def test_create_preserves_all_fields(self):
        job = Job(input_type="local_path", source_path="/tmp/contracts")
        self.store.create(job)
        fetched = self.store.get(job.job_id)
        self.assertEqual(fetched.input_type, "local_path")
        self.assertEqual(fetched.source_path, "/tmp/contracts")
        self.assertEqual(fetched.status, JobStatus.QUEUED)


@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBJobStoreUpdate(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=TABLE_NAME, region=REGION)

    def tearDown(self):
        self.mock.stop()

    def test_update_status(self):
        job = Job(input_type="demo")
        self.store.create(job)
        updated = self.store.update(job.job_id, status=JobStatus.RUNNING)
        self.assertEqual(updated.status, JobStatus.RUNNING)

    def test_update_persists(self):
        job = Job(input_type="demo")
        self.store.create(job)
        self.store.update(job.job_id, status=JobStatus.SUCCEEDED, error_message=None)
        fetched = self.store.get(job.job_id)
        self.assertEqual(fetched.status, JobStatus.SUCCEEDED)

    def test_update_nonexistent_returns_none(self):
        result = self.store.update("ghost-id", status=JobStatus.FAILED)
        self.assertIsNone(result)

    def test_update_does_not_affect_other_jobs(self):
        job_a = Job(input_type="demo")
        job_b = Job(input_type="demo")
        self.store.create(job_a)
        self.store.create(job_b)
        self.store.update(job_a.job_id, status=JobStatus.RUNNING)
        fetched_b = self.store.get(job_b.job_id)
        self.assertEqual(fetched_b.status, JobStatus.QUEUED)


@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBJobStoreListAll(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=TABLE_NAME, region=REGION)

    def tearDown(self):
        self.mock.stop()

    def test_list_all_empty(self):
        self.assertEqual(self.store.list_all(), [])

    def test_list_all_returns_all_jobs(self):
        for _ in range(3):
            self.store.create(Job(input_type="demo"))
        jobs = self.store.list_all()
        self.assertEqual(len(jobs), 3)

    def test_list_all_sorted_by_created_at(self):
        j1 = Job(input_type="demo")
        j2 = Job(input_type="demo")
        j3 = Job(input_type="demo")
        for j in (j1, j2, j3):
            self.store.create(j)
        jobs = self.store.list_all()
        timestamps = [j.created_at for j in jobs]
        self.assertEqual(timestamps, sorted(timestamps))


# ─────────────────────────────────────────────────────────────────────────────
# worker_main config validation
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkerMainConfig(unittest.TestCase):

    def test_worker_main_importable(self):
        import trustgraph_cloud.worker_main  # must not raise

    def test_worker_main_has_main_function(self):
        from trustgraph_cloud.worker_main import main
        self.assertTrue(callable(main))

    def test_settings_accepts_dynamodb_job_store(self):
        from trustgraph_cloud.config import Settings
        s = Settings(
            job_store="dynamodb",
            dynamodb_table="trustgraph-jobs",
            dynamodb_region="us-east-1",
            job_queue="sqs",
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/test",
            artifact_store="s3",
            s3_bucket="my-bucket",
        )
        self.assertEqual(s.job_store, "dynamodb")
        self.assertEqual(s.dynamodb_table, "trustgraph-jobs")

    def test_settings_worker_only_default_false(self):
        from trustgraph_cloud.config import Settings
        s = Settings()
        self.assertFalse(s.worker_only)

    def test_settings_worker_only_set_true(self):
        from trustgraph_cloud.config import Settings
        s = Settings(worker_only=True)
        self.assertTrue(s.worker_only)

    def test_settings_demo_source_path_default_none(self):
        from trustgraph_cloud.config import Settings
        self.assertIsNone(Settings().demo_source_path)

    def test_settings_demo_source_path_explicit(self):
        from trustgraph_cloud.config import Settings
        s = Settings(demo_source_path="/build/examples/vulnerable-crosschain/src")
        self.assertEqual(s.demo_source_path, "/build/examples/vulnerable-crosschain/src")


# ─────────────────────────────────────────────────────────────────────────────
# CDK stack synthesis
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_CDK, "aws-cdk-lib not installed (install infra/cdk/requirements.txt)")
class TestCDKStack(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        cls.stack = TrustGraphWorkerStack(
            app,
            "TestStack",
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )
        cls.template = cdk_assertions.Template.from_stack(cls.stack)

    def test_stack_synthesizes(self):
        # Synthesis succeeded if setUpClass didn't raise.
        self.assertIsNotNone(self.template)

    def test_has_sqs_queue(self):
        self.template.resource_count_is("AWS::SQS::Queue", 2)  # main + DLQ

    def test_has_s3_bucket(self):
        self.template.resource_count_is("AWS::S3::Bucket", 1)

    def test_has_dynamodb_table(self):
        # Phase 2D added auth tables: jobs + users + api-keys = 3
        self.template.resource_count_is("AWS::DynamoDB::Table", 3)

    def test_has_ecs_cluster(self):
        self.template.resource_count_is("AWS::ECS::Cluster", 1)

    def test_has_fargate_task_definition(self):
        # Phase 2D added API task definition: worker + api = 2
        self.template.resource_count_is("AWS::ECS::TaskDefinition", 2)

    def test_has_fargate_service(self):
        # Phase 2D added API service: worker + api = 2
        self.template.resource_count_is("AWS::ECS::Service", 2)

    def test_task_definition_references_worker_image(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Image": WORKER_IMAGE_URI,
                    })
                ])
            }),
        )

    def test_task_definition_has_required_env_vars(self):
        required = {
            "TRUSTGRAPH_JOB_QUEUE": "sqs",
            "TRUSTGRAPH_ARTIFACT_STORE": "s3",
            "TRUSTGRAPH_EXECUTION_MODE": "local_host",
            "TRUSTGRAPH_JOB_STORE": "dynamodb",
            "TRUSTGRAPH_WORKER_ONLY": "true",
        }
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({"Name": k, "Value": v})
                            for k, v in required.items()
                        ])
                    })
                ])
            }),
        )

    def test_task_role_has_sqs_actions(self):
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Action": cdk_assertions.Match.array_with([
                                "sqs:ReceiveMessage",
                                "sqs:DeleteMessage",
                                "sqs:GetQueueAttributes",
                                "sqs:ChangeMessageVisibility",
                            ]),
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )

    def test_task_role_has_s3_actions(self):
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Action": cdk_assertions.Match.array_with(["s3:PutObject", "s3:GetObject"]),
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )

    def test_task_role_has_dynamodb_actions(self):
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Action": cdk_assertions.Match.array_with([
                                "dynamodb:GetItem",
                                "dynamodb:PutItem",
                                "dynamodb:Scan",
                            ]),
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )

    def test_log_group_retention_7_days(self):
        self.template.has_resource_properties(
            "AWS::Logs::LogGroup",
            {"RetentionInDays": 7},
        )

    def test_s3_lifecycle_expiration_14_days(self):
        self.template.has_resource_properties(
            "AWS::S3::Bucket",
            cdk_assertions.Match.object_like({
                "LifecycleConfiguration": cdk_assertions.Match.object_like({
                    "Rules": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "ExpirationInDays": 14,
                        })
                    ])
                })
            }),
        )

    def test_no_nat_gateway(self):
        # Cost safety: no NAT gateways in the stack.
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)

    def test_desired_count_default_1(self):
        self.template.has_resource_properties(
            "AWS::ECS::Service",
            {"DesiredCount": 1},
        )

    def test_task_definition_has_demo_source_path(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_DEMO_SOURCE_PATH",
                                "Value": "/build/examples/vulnerable-crosschain/src",
                            })
                        ])
                    })
                ])
            }),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile sanity checks
# ─────────────────────────────────────────────────────────────────────────────

class TestDockerfileWorker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        dockerfile = Path(__file__).parent.parent.parent / "Dockerfile.worker"
        cls.content = dockerfile.read_text(encoding="utf-8")

    def test_dockerfile_copies_examples(self):
        self.assertIn("COPY examples/", self.content)

    def test_dockerfile_uses_worker_entrypoint(self):
        self.assertIn('ENTRYPOINT ["trustgraph-worker"]', self.content)

    def test_dockerfile_copies_trustgraph_cloud(self):
        self.assertIn("COPY trustgraph_cloud/", self.content)

    def test_dockerfile_installs_cloud_extras(self):
        self.assertIn("[gemini,cloud]", self.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
