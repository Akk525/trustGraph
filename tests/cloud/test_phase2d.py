"""
Phase 2D tests — API Fargate service, Application Load Balancer, auth DynamoDB
tables, JWT secret in Secrets Manager, and Dockerfile.api sanity checks.

CDK tests require aws-cdk-lib; skipped when the package is absent.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import aws_cdk as cdk
    from aws_cdk import assertions as cdk_assertions
    _CDK_DIR = Path(__file__).parent.parent.parent / "infra" / "cdk"
    sys.path.insert(0, str(_CDK_DIR))
    from stacks.trustgraph_worker_stack import (
        TrustGraphWorkerStack,
        API_IMAGE_URI,
        API_LOG_GROUP_NAME,
        API_SERVICE_NAME,
        API_KEYS_TABLE_NAME,
        JWT_SECRET_NAME,
        USERS_TABLE_NAME,
        WORKER_IMAGE_URI,
    )
    HAS_CDK = True
except ImportError:
    HAS_CDK = False


@unittest.skipUnless(HAS_CDK, "aws-cdk-lib not installed (install infra/cdk/requirements.txt)")
class TestCDKPhase2D(unittest.TestCase):
    """CDK assertion tests for Phase 2D additions."""

    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        cls.stack = TrustGraphWorkerStack(
            app,
            "TestStack",
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )
        cls.template = cdk_assertions.Template.from_stack(cls.stack)

    # ------------------------------------------------------------------
    # Top-level counts
    # ------------------------------------------------------------------

    def test_stack_synthesizes(self):
        self.assertIsNotNone(self.template)

    def test_has_two_fargate_services(self):
        self.template.resource_count_is("AWS::ECS::Service", 2)

    def test_has_two_task_definitions(self):
        self.template.resource_count_is("AWS::ECS::TaskDefinition", 2)

    def test_has_three_dynamodb_tables(self):
        # jobs + users + api-keys
        self.template.resource_count_is("AWS::DynamoDB::Table", 3)

    def test_has_one_alb(self):
        self.template.resource_count_is("AWS::ElasticLoadBalancingV2::LoadBalancer", 1)

    def test_has_alb_listener_on_port_80(self):
        self.template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::Listener",
            {"Port": 80, "Protocol": "HTTP"},
        )

    def test_has_target_group_with_health_check(self):
        self.template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::TargetGroup",
            cdk_assertions.Match.object_like({
                "HealthCheckPath": "/health",
                "HealthCheckIntervalSeconds": 30,
            }),
        )

    def test_target_group_routes_to_port_8000(self):
        self.template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::TargetGroup",
            cdk_assertions.Match.object_like({
                "Port": 8000,
                "Protocol": "HTTP",
            }),
        )

    # ------------------------------------------------------------------
    # Auth DynamoDB tables
    # ------------------------------------------------------------------

    def test_has_users_table(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {"TableName": USERS_TABLE_NAME},
        )

    def test_has_api_keys_table(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {"TableName": API_KEYS_TABLE_NAME},
        )

    def test_auth_tables_pay_per_request(self):
        # Both auth tables use on-demand billing
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {"TableName": USERS_TABLE_NAME, "BillingMode": "PAY_PER_REQUEST"},
        )
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {"TableName": API_KEYS_TABLE_NAME, "BillingMode": "PAY_PER_REQUEST"},
        )

    # ------------------------------------------------------------------
    # Secrets Manager
    # ------------------------------------------------------------------

    def test_has_jwt_secret(self):
        self.template.has_resource_properties(
            "AWS::SecretsManager::Secret",
            {"Name": JWT_SECRET_NAME},
        )

    # ------------------------------------------------------------------
    # API task definition — env vars
    # ------------------------------------------------------------------

    def test_api_task_def_embedded_worker_disabled(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_EMBEDDED_WORKER",
                                "Value": "false",
                            })
                        ])
                    })
                ])
            }),
        )

    def test_api_task_def_auth_required_true(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_AUTH_REQUIRED",
                                "Value": "true",
                            })
                        ])
                    })
                ])
            }),
        )

    def test_api_task_def_auth_store_dynamodb(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_AUTH_STORE",
                                "Value": "dynamodb",
                            })
                        ])
                    })
                ])
            }),
        )

    def test_api_task_def_references_api_image(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Image": API_IMAGE_URI,
                    })
                ])
            }),
        )

    def test_api_task_def_exposes_port_8000(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "PortMappings": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "ContainerPort": 8000,
                            })
                        ])
                    })
                ])
            }),
        )

    # ------------------------------------------------------------------
    # API task role — IAM permissions
    # ------------------------------------------------------------------

    def test_api_task_role_has_sqs_send_message(self):
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Action": cdk_assertions.Match.array_with([
                                "sqs:SendMessage",
                                "sqs:GetQueueAttributes",
                            ]),
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )

    def test_api_task_role_has_s3_put_for_presigned_uploads(self):
        # CDK renders single-action policies as a string, not an array.
        # Identify by Sid to avoid the string-vs-array ambiguity.
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Sid": "S3InputUpload",
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )

    def test_api_task_role_has_dynamodb_permissions(self):
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

    # ------------------------------------------------------------------
    # API log group
    # ------------------------------------------------------------------

    def test_api_log_group_exists(self):
        self.template.has_resource_properties(
            "AWS::Logs::LogGroup",
            {"LogGroupName": API_LOG_GROUP_NAME},
        )

    def test_api_log_group_retention_7_days(self):
        self.template.has_resource_properties(
            "AWS::Logs::LogGroup",
            {
                "LogGroupName": API_LOG_GROUP_NAME,
                "RetentionInDays": 7,
            },
        )

    # ------------------------------------------------------------------
    # Worker task unchanged
    # ------------------------------------------------------------------

    def test_worker_task_still_references_worker_image(self):
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

    def test_worker_task_still_has_worker_only_true(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_WORKER_ONLY",
                                "Value": "true",
                            })
                        ])
                    })
                ])
            }),
        )

    # ------------------------------------------------------------------
    # Cost guard
    # ------------------------------------------------------------------

    def test_no_nat_gateway(self):
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)

    def test_api_desired_count_default_1(self):
        # Both services have desired_count=1 by default
        self.template.has_resource_properties(
            "AWS::ECS::Service",
            {"DesiredCount": 1},
        )


# ---------------------------------------------------------------------------
# Dockerfile.api sanity checks
# ---------------------------------------------------------------------------

class TestDockerfileApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        dockerfile = Path(__file__).parent.parent.parent / "Dockerfile.api"
        cls.content = dockerfile.read_text(encoding="utf-8")

    def test_dockerfile_exists(self):
        self.assertIsNotNone(self.content)
        self.assertGreater(len(self.content), 0)

    def test_dockerfile_uses_python_312(self):
        self.assertIn("python:3.12", self.content)

    def test_dockerfile_installs_cloud_extras(self):
        self.assertIn("[cloud]", self.content)

    def test_dockerfile_uses_uvicorn(self):
        self.assertIn("uvicorn", self.content)

    def test_dockerfile_uses_correct_app_module(self):
        self.assertIn("trustgraph_cloud.api.main:app", self.content)

    def test_dockerfile_exposes_port_8000(self):
        self.assertIn("EXPOSE 8000", self.content)

    def test_dockerfile_does_not_use_worker_entrypoint(self):
        self.assertNotIn('ENTRYPOINT ["trustgraph-worker"]', self.content)

    def test_dockerfile_does_not_copy_examples(self):
        # API does not need examples/ — scanner runs in the worker container
        self.assertNotIn("COPY examples/", self.content)

    def test_dockerfile_copies_trustgraph_cloud(self):
        self.assertIn("COPY trustgraph_cloud/", self.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
